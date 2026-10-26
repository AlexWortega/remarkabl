//! Native qtfb client: SOCK_SEQPACKET protocol + shared-memory framebuffer.
//!
//! Wire format (verified against rm-appload src/qtfb/common.h):
//!   ClientMessage payload starts at byte 4 on both architectures.
//!   ServerMessage payload starts at byte 4 on ARM32 and byte 8 on AArch64.

use std::io;
use std::os::fd::RawFd;

pub const MESSAGE_INITIALIZE: u8 = 0;
pub const MESSAGE_UPDATE: u8 = 1;
#[allow(dead_code)]
pub const MESSAGE_CUSTOM_INITIALIZE: u8 = 2;
pub const MESSAGE_TERMINATE: u8 = 3;
pub const MESSAGE_USERINPUT: u8 = 4;
pub const MESSAGE_SET_REFRESH_MODE: u8 = 5;
pub const MESSAGE_REQUEST_FULL_REFRESH: u8 = 6;

pub const UPDATE_ALL: i32 = 0;
pub const UPDATE_PARTIAL: i32 = 1;

/// FBFMT_RM2FB: native 1404x1872 RGB565.
#[cfg(feature = "rm2")]
pub const FBFMT_RM2FB: u8 = 0;
/// FBFMT_RMPP_RGB565: native 1620x2160 RGB565.
#[cfg(not(feature = "rm2"))]
pub const FBFMT_RMPP_RGB565: u8 = 3;

#[cfg(target_pointer_width = "32")]
const SERVER_PAYLOAD_OFFSET: usize = 4;
#[cfg(target_pointer_width = "32")]
const INIT_SIZE_OFFSET: usize = 8;
#[cfg(target_pointer_width = "32")]
const INIT_SIZE_WIDTH: usize = 4;

#[cfg(target_pointer_width = "64")]
const SERVER_PAYLOAD_OFFSET: usize = 8;
#[cfg(target_pointer_width = "64")]
const INIT_SIZE_OFFSET: usize = 16;
#[cfg(target_pointer_width = "64")]
const INIT_SIZE_WIDTH: usize = 8;

#[allow(dead_code)]
pub const REFRESH_MODE_UFAST: i32 = 0;
#[allow(dead_code)]
pub const REFRESH_MODE_FAST: i32 = 1;

// Input event types (server -> client).
#[allow(dead_code)]
pub const INPUT_TOUCH_PRESS: i32 = 0x10;
#[allow(dead_code)]
pub const INPUT_TOUCH_RELEASE: i32 = 0x11;
#[allow(dead_code)]
pub const INPUT_TOUCH_UPDATE: i32 = 0x12;
pub const INPUT_PEN_PRESS: i32 = 0x20;
pub const INPUT_PEN_RELEASE: i32 = 0x21;
#[allow(dead_code)]
pub const INPUT_PEN_UPDATE: i32 = 0x22;
#[allow(dead_code)]
pub const INPUT_VKB_PRESS: i32 = 0x40;
#[allow(dead_code)]
pub const INPUT_VKB_RELEASE: i32 = 0x41;

const SOCKET_PATH: &str = "/tmp/qtfb.sock";

#[derive(Debug, Clone, Copy)]
pub struct InputEvent {
    pub input_type: i32,
    #[allow(dead_code)]
    pub dev_id: i32,
    pub x: i32,
    pub y: i32,
    #[allow(dead_code)]
    pub d: i32,
}

pub struct QtfbClient {
    fd: RawFd,
    shm: *mut u8,
    shm_len: usize,
    #[allow(dead_code)]
    pub width: usize,
    #[allow(dead_code)]
    pub height: usize,
    /// bytes per pixel
    #[allow(dead_code)]
    pub bpp: usize,
}

// The raw pointer is to a MAP_SHARED region; we are the only writer thread.
unsafe impl Send for QtfbClient {}

impl QtfbClient {
    /// Connect and initialize with the default resolution of `format`.
    pub fn connect(
        key: i32,
        format: u8,
        width: usize,
        height: usize,
        bpp: usize,
    ) -> io::Result<Self> {
        let fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_SEQPACKET, 0) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }

        let mut addr: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        addr.sun_family = libc::AF_UNIX as libc::sa_family_t;
        for (i, b) in SOCKET_PATH.bytes().enumerate() {
            addr.sun_path[i] = b as libc::c_char;
        }
        let rc = unsafe {
            libc::connect(
                fd,
                &addr as *const _ as *const libc::sockaddr,
                std::mem::size_of::<libc::sockaddr_un>() as libc::socklen_t,
            )
        };
        if rc != 0 {
            let e = io::Error::last_os_error();
            unsafe { libc::close(fd) };
            return Err(e);
        }

        // MESSAGE_INITIALIZE: key i32 @4, format u8 @8.
        let mut msg = [0u8; 24];
        msg[0] = MESSAGE_INITIALIZE;
        msg[4..8].copy_from_slice(&key.to_le_bytes());
        msg[8] = format;
        send_all(fd, &msg)?;

        // ServerMessage contains a pointer-width-aligned union. Its offsets
        // therefore differ between the ARM32 rM2 and AArch64 Paper Pro.
        let mut reply = [0u8; 32];
        let n = unsafe { libc::recv(fd, reply.as_mut_ptr() as *mut libc::c_void, 32, 0) };
        if n <= 0 {
            unsafe { libc::close(fd) };
            return Err(io::Error::new(
                io::ErrorKind::ConnectionReset,
                "qtfb server rejected init (no reply)",
            ));
        }
        let (shm_key, shm_size) = decode_init_response(
            &reply[..n as usize],
            SERVER_PAYLOAD_OFFSET,
            INIT_SIZE_OFFSET,
            INIT_SIZE_WIDTH,
        )?;

        let shm_name = format!("/qtfb_{}\0", shm_key);
        let shm_fd =
            unsafe { libc::shm_open(shm_name.as_ptr() as *const libc::c_char, libc::O_RDWR, 0) };
        if shm_fd < 0 {
            let e = io::Error::last_os_error();
            unsafe { libc::close(fd) };
            return Err(e);
        }
        let ptr = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                shm_size,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                shm_fd,
                0,
            )
        };
        unsafe { libc::close(shm_fd) };
        if ptr == libc::MAP_FAILED {
            let e = io::Error::last_os_error();
            unsafe { libc::close(fd) };
            return Err(e);
        }

        if shm_size < width * height * bpp {
            unsafe { libc::close(fd) };
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("shm too small: {} < {}", shm_size, width * height * bpp),
            ));
        }

        // Non-blocking: the event loop drains input events opportunistically.
        unsafe {
            let flags = libc::fcntl(fd, libc::F_GETFL);
            libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK);
        }

        Ok(Self {
            fd,
            shm: ptr as *mut u8,
            shm_len: shm_size,
            width,
            height,
            bpp,
        })
    }

    #[allow(dead_code)]
    pub fn raw_fd(&self) -> RawFd {
        self.fd
    }

    pub fn framebuffer(&mut self) -> &mut [u8] {
        unsafe { std::slice::from_raw_parts_mut(self.shm, self.shm_len) }
    }

    fn send_msg(&self, msg: &[u8; 24]) -> io::Result<()> {
        send_all(self.fd, msg)
    }

    pub fn update_all(&self) -> io::Result<()> {
        let mut msg = [0u8; 24];
        msg[0] = MESSAGE_UPDATE;
        msg[4..8].copy_from_slice(&UPDATE_ALL.to_le_bytes());
        self.send_msg(&msg)
    }

    pub fn update_partial(&self, x: i32, y: i32, w: i32, h: i32) -> io::Result<()> {
        let mut msg = [0u8; 24];
        msg[0] = MESSAGE_UPDATE;
        msg[4..8].copy_from_slice(&UPDATE_PARTIAL.to_le_bytes());
        msg[8..12].copy_from_slice(&x.to_le_bytes());
        msg[12..16].copy_from_slice(&y.to_le_bytes());
        msg[16..20].copy_from_slice(&w.to_le_bytes());
        msg[20..24].copy_from_slice(&h.to_le_bytes());
        self.send_msg(&msg)
    }

    /// NOTE: the server sleeps its handler thread for 1s after this — call rarely.
    pub fn set_refresh_mode(&self, mode: i32) -> io::Result<()> {
        let mut msg = [0u8; 24];
        msg[0] = MESSAGE_SET_REFRESH_MODE;
        msg[4..8].copy_from_slice(&mode.to_le_bytes());
        self.send_msg(&msg)
    }

    /// NOTE: 1s server-side stall, use only on explicit user request.
    pub fn request_full_refresh(&self) -> io::Result<()> {
        let mut msg = [0u8; 24];
        msg[0] = MESSAGE_REQUEST_FULL_REFRESH;
        self.send_msg(&msg)
    }

    pub fn terminate(&self) {
        let mut msg = [0u8; 24];
        msg[0] = MESSAGE_TERMINATE;
        let _ = self.send_msg(&msg);
    }

    /// Drain pending server messages. Returns input events, or Err on
    /// disconnect (window closed -> we must exit).
    pub fn drain_events(&self) -> io::Result<Vec<InputEvent>> {
        let mut out = Vec::new();
        loop {
            let mut buf = [0u8; 32];
            let n = unsafe { libc::recv(self.fd, buf.as_mut_ptr() as *mut libc::c_void, 32, 0) };
            if n == 0 {
                return Err(io::Error::new(
                    io::ErrorKind::ConnectionReset,
                    "qtfb socket closed",
                ));
            }
            if n < 0 {
                let e = io::Error::last_os_error();
                if e.kind() == io::ErrorKind::WouldBlock {
                    return Ok(out);
                }
                if e.kind() == io::ErrorKind::Interrupted {
                    continue;
                }
                return Err(e);
            }
            if buf[0] == MESSAGE_USERINPUT {
                out.push(decode_input_event(
                    &buf[..n as usize],
                    SERVER_PAYLOAD_OFFSET,
                )?);
            }
        }
    }
}

fn decode_init_response(
    reply: &[u8],
    payload_offset: usize,
    size_offset: usize,
    size_width: usize,
) -> io::Result<(i32, usize)> {
    let needed = (payload_offset + 4).max(size_offset + size_width);
    if reply.len() < needed {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("short qtfb init response: {} < {needed}", reply.len()),
        ));
    }

    let shm_key = i32::from_le_bytes(
        reply[payload_offset..payload_offset + 4]
            .try_into()
            .unwrap(),
    );
    let shm_size = match size_width {
        4 => u32::from_le_bytes(reply[size_offset..size_offset + 4].try_into().unwrap()) as usize,
        8 => usize::try_from(u64::from_le_bytes(
            reply[size_offset..size_offset + 8].try_into().unwrap(),
        ))
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "qtfb shm size overflows usize"))?,
        _ => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "unsupported size_t width",
            ))
        }
    };
    Ok((shm_key, shm_size))
}

fn decode_input_event(reply: &[u8], payload_offset: usize) -> io::Result<InputEvent> {
    let needed = payload_offset + 20;
    if reply.len() < needed {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("short qtfb input event: {} < {needed}", reply.len()),
        ));
    }

    let field = |offset: usize| i32::from_le_bytes(reply[offset..offset + 4].try_into().unwrap());
    Ok(InputEvent {
        input_type: field(payload_offset),
        dev_id: field(payload_offset + 4),
        x: field(payload_offset + 8),
        y: field(payload_offset + 12),
        d: field(payload_offset + 16),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_arm32_server_messages() {
        let mut init = [0u8; 24];
        init[4..8].copy_from_slice(&1234i32.to_le_bytes());
        init[8..12].copy_from_slice(&5_256_576u32.to_le_bytes());
        assert_eq!(
            decode_init_response(&init, 4, 8, 4).unwrap(),
            (1234, 5_256_576)
        );

        let mut input = [0u8; 24];
        for (offset, value) in [(4, INPUT_PEN_PRESS), (8, 2), (12, 700), (16, 900), (20, 85)] {
            input[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
        }
        let event = decode_input_event(&input, 4).unwrap();
        assert_eq!(
            (event.input_type, event.dev_id, event.x, event.y, event.d),
            (32, 2, 700, 900, 85)
        );
    }

    #[test]
    fn decodes_aarch64_server_messages() {
        let mut init = [0u8; 32];
        init[8..12].copy_from_slice(&4321i32.to_le_bytes());
        init[16..24].copy_from_slice(&6_998_400u64.to_le_bytes());
        assert_eq!(
            decode_init_response(&init, 8, 16, 8).unwrap(),
            (4321, 6_998_400)
        );
    }

    #[test]
    fn rejects_short_messages() {
        assert!(decode_init_response(&[0; 8], 4, 8, 4).is_err());
        assert!(decode_input_event(&[0; 20], 4).is_err());
    }
}

impl Drop for QtfbClient {
    fn drop(&mut self) {
        self.terminate();
        unsafe {
            libc::munmap(self.shm as *mut libc::c_void, self.shm_len);
            libc::close(self.fd);
        }
    }
}

fn send_all(fd: RawFd, buf: &[u8]) -> io::Result<()> {
    loop {
        let n = unsafe { libc::send(fd, buf.as_ptr() as *const libc::c_void, buf.len(), 0) };
        if n == buf.len() as isize {
            return Ok(());
        }
        if n < 0 {
            let e = io::Error::last_os_error();
            if e.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            // Non-blocking socket: retry sends briefly rather than dropping a
            // protocol message (updates are small and the server drains fast).
            if e.kind() == io::ErrorKind::WouldBlock {
                std::thread::sleep(std::time::Duration::from_millis(2));
                continue;
            }
            return Err(e);
        }
        return Err(io::Error::new(io::ErrorKind::WriteZero, "short send"));
    }
}
