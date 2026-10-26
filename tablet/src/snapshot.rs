//! Read the existing rM2 image without invoking the Qt graphics renderer.
use std::{fs, io, os::unix::fs::FileExt, process::Command};

const WIDTH: usize = 1872;
const HEIGHT: usize = 1404;
const FRAME_BYTES: usize = WIDTH * HEIGHT * 2;

fn frame_address(maps: &str) -> io::Result<u64> {
    let lines: Vec<_> = maps.lines().collect();
    for pair in lines.windows(2) {
        let device: Vec<_> = pair[0].split_whitespace().collect();
        if device.len() != 6 || device[5] != "/dev/fb0" {
            continue;
        }
        let buffer: Vec<_> = pair[1].split_whitespace().collect();
        if buffer.len() != 5
            || buffer[1] != "rw-p"
            || buffer[2] != "00000000"
            || buffer[3] != "00:00"
            || buffer[4] != "0"
        {
            continue;
        }
        let bounds = |range: &str| -> Option<(u64, u64)> {
            let (start, end) = range.split_once('-')?;
            Some((
                u64::from_str_radix(start, 16).ok()?,
                u64::from_str_radix(end, 16).ok()?,
            ))
        };
        if let (Some((_, device_end)), Some((start, end))) = (bounds(device[0]), bounds(buffer[0]))
        {
            if start == device_end
                && end
                    .checked_sub(start)
                    .is_some_and(|n| n >= FRAME_BYTES as u64 + 8)
            {
                return Ok(start + 8);
            }
        }
    }
    Err(io::Error::other("Supported framebuffer mapping not found"))
}

fn portrait_pixels(raw: &[u8], width: usize, height: usize) -> io::Result<Vec<u8>> {
    if raw.len() != width * height * 2 {
        return Err(io::Error::other("Incomplete framebuffer"));
    }
    let mut pixels = vec![0; width * height];
    for (i, word) in raw.chunks_exact(2).enumerate() {
        let gray = u16::from_le_bytes([word[0], word[1]]);
        if gray > 31 {
            return Err(io::Error::other("Unexpected framebuffer pixel format"));
        }
        // Transpose across the antidiagonal: landscape memory to upright portrait.
        let x = height - 1 - i / width;
        let y = width - 1 - i % width;
        pixels[y * height + x] = (gray.min(30) * 255 / 30) as u8;
    }
    Ok(pixels)
}

fn xochitl_pid() -> io::Result<String> {
    let output = Command::new("systemctl")
        .args(["show", "xochitl", "--property=MainPID", "--value"])
        .output()?;
    let pid = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    if !output.status.success() || pid.parse::<u32>().ok().is_none_or(|n| n == 0) {
        return Err(io::Error::other("Native reader is not running"));
    }
    if fs::read_link(format!("/proc/{pid}/exe"))?
        .file_name()
        .is_none_or(|name| name != "xochitl")
    {
        return Err(io::Error::other("Native reader process mismatch"));
    }
    Ok(pid)
}

pub fn capture() -> io::Result<Vec<u8>> {
    if fs::read_to_string("/sys/devices/soc0/machine")?.trim() != "reMarkable 2.0"
        || !fs::read_to_string("/etc/os-release")?.lines().any(|line| {
            line.strip_prefix("IMG_VERSION=")
                .is_some_and(|v| v.trim_matches('"') == "3.20.0.92")
        })
    {
        return Err(io::Error::other(
            "Unsupported tablet or firmware for screen capture",
        ));
    }
    let pid = xochitl_pid()?;
    let address = frame_address(&fs::read_to_string(format!("/proc/{pid}/maps"))?)?;
    let mut raw = vec![0; FRAME_BYTES];
    // Only this validated image buffer is read. The process is never modified or paused.
    fs::File::open(format!("/proc/{pid}/mem"))?.read_exact_at(&mut raw, address)?;
    if xochitl_pid()? != pid {
        return Err(io::Error::other("Native reader changed during capture"));
    }
    let pixels = portrait_pixels(&raw, WIDTH, HEIGHT)?;
    drop(raw);
    let mut png = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut png, HEIGHT as u32, WIDTH as u32);
        encoder.set_color(png::ColorType::Grayscale);
        encoder.set_compression(png::Compression::Fast);
        encoder.write_header()?.write_image_data(&pixels)?;
    }
    Ok(png)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn locates_only_the_supported_complete_image_mapping() {
        let maps = "6ecb8000-70476000 rw-s a9d00000 00:06 7301 /dev/fb0\n70476000-71121000 rw-p 00000000 00:00 0\n";
        assert_eq!(frame_address(maps).unwrap(), 0x70476008);
        for bad in [
            maps.replace("/dev/fb0", "/dev/fb1"),
            maps.replace("71121000", "70477000"),
            maps.replace("rw-p", "r--p"),
            maps.replace("00:00 0", "00:00 0 /other"),
        ] {
            assert!(frame_address(&bad).is_err());
        }
    }

    #[test]
    fn rotates_grayscale_to_portrait_and_rejects_other_memory() {
        let raw: Vec<_> = [0u16, 6, 12, 18, 24, 30]
            .into_iter()
            .flat_map(u16::to_le_bytes)
            .collect();
        assert_eq!(
            portrait_pixels(&raw, 3, 2).unwrap(),
            [255, 102, 204, 51, 153, 0]
        );
        assert!(portrait_pixels(&raw[..10], 3, 2).is_err());
        assert!(portrait_pixels(&[255, 255], 1, 1).is_err());
    }
}
