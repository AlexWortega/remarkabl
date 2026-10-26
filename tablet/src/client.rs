use base64::{engine::general_purpose::STANDARD, Engine};
use fs2::FileExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

pub type Strokes = Vec<Vec<(i32, i32, i32)>>;

#[derive(Serialize, Deserialize)]
pub struct Request {
    pub id: String,
    pub conversation_id: String,
    pub image_base64: String,
    pub strokes: Strokes,
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    pub page_capture: bool,
}

pub fn root() -> PathBuf {
    std::env::var_os("REMARKABL_DATA_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/home/root/remarkabl"))
}

pub fn atomic(path: &Path, bytes: &[u8]) -> io::Result<()> {
    if fs::read(path).is_ok_and(|previous| previous == bytes) {
        return Ok(());
    }
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension(format!("tmp-{}", uuid::Uuid::new_v4()));
    let mut file = OpenOptions::new().create_new(true).write(true).open(&tmp)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(0o600))?;
    }
    file.write_all(bytes)?;
    file.sync_all()?;
    fs::rename(&tmp, path)?;
    if let Some(parent) = path.parent() {
        File::open(parent)?.sync_all()?;
    }
    Ok(())
}

pub fn conversation(dir: &Path) -> io::Result<String> {
    let path = dir.join("conversation-id");
    if let Ok(id) = fs::read_to_string(&path) {
        return Ok(id.trim().to_string());
    }
    let id = uuid::Uuid::new_v4().to_string();
    atomic(&path, id.as_bytes())?;
    Ok(id)
}

pub fn save_draft(dir: &Path, strokes: &Strokes) -> io::Result<()> {
    atomic(&dir.join("draft.json"), &serde_json::to_vec(strokes)?)
}

enum DraftMessage {
    Save(Strokes),
    Flush(std::sync::mpsc::Sender<Result<(), String>>),
}

pub struct DraftWriter(std::sync::mpsc::Sender<DraftMessage>);

impl DraftWriter {
    pub fn new(dir: PathBuf) -> Self {
        let (sender, receiver) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut error = None;
            while let Ok(message) = receiver.recv() {
                match message {
                    DraftMessage::Save(strokes) => {
                        error = save_draft(&dir, &strokes).err().map(|e| e.to_string());
                    }
                    DraftMessage::Flush(reply) => {
                        let _ = reply.send(error.clone().map_or(Ok(()), Err));
                    }
                }
            }
        });
        Self(sender)
    }

    pub fn save(&self, strokes: Strokes) -> io::Result<()> {
        self.0
            .send(DraftMessage::Save(strokes))
            .map_err(io::Error::other)
    }

    pub fn flush(&self) -> io::Result<()> {
        let (sender, receiver) = std::sync::mpsc::channel();
        self.0
            .send(DraftMessage::Flush(sender))
            .map_err(io::Error::other)?;
        receiver
            .recv()
            .map_err(io::Error::other)?
            .map_err(io::Error::other)
    }
}

pub fn draft(dir: &Path) -> Strokes {
    fs::read(dir.join("draft.json"))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}

pub fn enqueue(dir: &Path, png: &[u8], strokes: Strokes) -> io::Result<String> {
    enqueue_mode(dir, png, strokes, false)
}

pub fn enqueue_mode(
    dir: &Path,
    png: &[u8],
    strokes: Strokes,
    page_capture: bool,
) -> io::Result<String> {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let id = format!("{millis:020}-{}", uuid::Uuid::new_v4());
    let turn = Request {
        id: id.clone(),
        conversation_id: conversation(dir)?,
        image_base64: STANDARD.encode(png),
        strokes,
        page_capture,
    };
    atomic(
        &dir.join("outbox").join(format!("{id}.request.json")),
        &serde_json::to_vec(&turn)?,
    )?;
    // Clearing the draft happens only after the complete request is fsynced.
    if !page_capture {
        save_draft(dir, &Vec::new())?;
    }
    wake_sync(dir);
    Ok(id)
}

fn wake_sync(dir: &Path) {
    if let Ok(socket) = std::os::unix::net::UnixDatagram::unbound() {
        let _ = socket.send_to(b"wake", dir.join("sync-wake.sock"));
    }
}

pub fn requests(dir: &Path) -> Vec<PathBuf> {
    let mut items: Vec<PathBuf> = fs::read_dir(dir.join("outbox"))
        .into_iter()
        .flatten()
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.to_string_lossy().ends_with(".request.json"))
        .collect();
    items.sort();
    items
}

pub fn id_for(path: &Path) -> String {
    path.file_name()
        .unwrap()
        .to_string_lossy()
        .trim_end_matches(".request.json")
        .to_string()
}

pub fn result(dir: &Path, id: &str) -> Option<Value> {
    fs::read(dir.join("outbox").join(format!("{id}.result.json")))
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
}

pub fn pending(dir: &Path) -> Vec<String> {
    requests(dir)
        .iter()
        .map(|path| id_for(path))
        .filter(|id| {
            !matches!(
                result(dir, id).as_ref().and_then(|v| v["status"].as_str()),
                Some("complete" | "error")
            )
        })
        .collect()
}

pub fn retry(dir: &Path, id: &str) -> io::Result<()> {
    atomic(&dir.join("outbox").join(format!("{id}.retry")), b"retry")?;
    let _ = fs::remove_file(dir.join("outbox").join(format!("{id}.result.json")));
    wake_sync(dir);
    Ok(())
}

pub fn sync_once(dir: &Path) -> io::Result<()> {
    let url = std::env::var("REMARKABL_URL").unwrap_or_default();
    let token = std::env::var("REMARKABL_TOKEN").unwrap_or_default();
    if url.is_empty() || token.is_empty() {
        return Err(io::Error::other("missing server configuration"));
    }
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(5))
        .timeout_read(Duration::from_secs(30))
        .timeout_write(Duration::from_secs(15))
        .build();
    for path in requests(dir) {
        let id = id_for(&path);
        let before = result(dir, &id);
        let retry_path = dir.join("outbox").join(format!("{id}.retry"));
        let auth = format!("Bearer {token}");
        if retry_path.exists() {
            let response = agent
                .post(&format!(
                    "{}/v1/turns/{id}/retry",
                    url.trim_end_matches('/')
                ))
                .set("Authorization", &auth)
                .send_json(json!({}));
            if response.is_err() {
                continue;
            }
            fs::remove_file(retry_path)?;
        } else if matches!(
            before.as_ref().and_then(|v| v["status"].as_str()),
            Some("complete" | "error")
        ) {
            continue;
        }
        let response = if before.is_none() {
            let turn: Request = serde_json::from_slice(&fs::read(&path)?)?;
            agent
                .post(&format!("{}/v1/turns", url.trim_end_matches('/')))
                .set("Authorization", &auth)
                .send_json(serde_json::to_value(turn)?)
        } else {
            agent
                .get(&format!("{}/v1/turns/{id}", url.trim_end_matches('/')))
                .set("Authorization", &auth)
                .call()
        };
        match response {
            Ok(response) => {
                let value: Value = response
                    .into_json()
                    .map_err(|_| io::Error::other("bad server response"))?;
                atomic(
                    &dir.join("outbox").join(format!("{id}.result.json")),
                    &serde_json::to_vec(&value)?,
                )?;
                let _ = fs::remove_file(dir.join("network-error"));
                // Preserve dialogue order: don't submit later turns until this one is answered.
                if value["status"] != "complete" && value["status"] != "error" {
                    receive_live(&agent, &url, &auth, dir, &id)?;
                    if result(dir, &id)
                        .is_none_or(|v| v["status"] != "complete" && v["status"] != "error")
                    {
                        break;
                    }
                }
            }
            Err(ureq::Error::Status(404, _)) => {
                // A restored server backup may not have the request. Re-send the same ID.
                let _ = fs::remove_file(dir.join("outbox").join(format!("{id}.result.json")));
                break;
            }
            Err(_) => {
                atomic(
                    &dir.join("network-error"),
                    "Нет связи. Вопрос сохранён до подключения.".as_bytes(),
                )?;
                break;
            }
        }
    }
    Ok(())
}

fn receive_live(
    agent: &ureq::Agent,
    url: &str,
    auth: &str,
    dir: &Path,
    id: &str,
) -> io::Result<()> {
    let response = match agent
        .get(&format!(
            "{}/v1/turns/{id}/events",
            url.trim_end_matches('/')
        ))
        .set("Authorization", auth)
        .call()
    {
        Ok(response) => response,
        Err(ureq::Error::Status(404, _)) => return Ok(()),
        Err(error) => return Err(io::Error::other(error)),
    };
    let mut reader = io::BufReader::new(response.into_reader());
    let mut last_write = std::time::Instant::now() - Duration::from_secs(1);
    let path = dir.join("outbox").join(format!("{id}.result.json"));
    loop {
        let mut line = String::new();
        if reader.read_line(&mut line)? == 0 {
            break;
        }
        if let Some(payload) = line.strip_prefix("data:") {
            let value: Value = serde_json::from_str(payload).map_err(io::Error::other)?;
            let done = value["status"] == "complete" || value["status"] == "error";
            if done || last_write.elapsed() >= Duration::from_millis(200) {
                atomic(&path, &serde_json::to_vec(&value)?)?;
                last_write = std::time::Instant::now();
            }
            if done {
                break;
            }
        }
    }
    Ok(())
}

pub fn sync_loop(dir: PathBuf, stop: Arc<AtomicBool>) {
    let _ = fs::create_dir_all(&dir);
    let lock = match OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(dir.join("sync.lock"))
    {
        Ok(file) => file,
        Err(_) => return,
    };
    if lock.try_lock_exclusive().is_err() {
        return;
    } // The systemd service already owns sync.
    let socket_path = dir.join("sync-wake.sock");
    let _ = fs::remove_file(&socket_path);
    let socket = std::os::unix::net::UnixDatagram::bind(&socket_path).ok();
    if let Some(socket) = &socket {
        let _ = socket.set_read_timeout(Some(Duration::from_millis(500)));
    }
    while !stop.load(Ordering::Relaxed) {
        if sync_once(&dir).is_err() {
            let _ = atomic(
                &dir.join("network-error"),
                "Нет связи. Вопрос сохранён до подключения.".as_bytes(),
            );
        }
        for _ in 0..10 {
            if stop.load(Ordering::Relaxed) {
                return;
            }
            if let Some(socket) = &socket {
                if socket.recv(&mut [0; 8]).is_ok() {
                    break;
                }
            } else {
                std::thread::sleep(Duration::from_millis(500));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn background_draft_flush_prevents_old_ink_from_reappearing_after_commit() {
        let dir = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let writer = DraftWriter::new(dir.clone());
        for i in 0..20 {
            writer.save(vec![vec![(i, 10, 2)]]).unwrap();
        }
        writer.flush().unwrap();
        assert_eq!(draft(&dir), vec![vec![(19, 10, 2)]]);
        enqueue(&dir, b"png", draft(&dir)).unwrap();
        writer.flush().unwrap();
        assert!(draft(&dir).is_empty());
        save_draft(&dir, &vec![vec![(5, 6, 2)]]).unwrap();
        enqueue_mode(&dir, b"page", Vec::new(), true).unwrap();
        assert_eq!(draft(&dir), vec![vec![(5, 6, 2)]]);
        drop(writer);
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn restart_preserves_unique_queued_turns_and_strokes() {
        let dir = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let strokes = vec![vec![(14, 25, 3), (18, 29, 2)]];
        save_draft(&dir, &strokes).unwrap();
        let a = enqueue(&dir, b"image-a", strokes.clone()).unwrap();
        let b = enqueue(&dir, b"image-b", strokes.clone()).unwrap();
        assert_ne!(a, b);
        assert!(draft(&dir).is_empty());
        let req: Request = serde_json::from_slice(&fs::read(&requests(&dir)[0]).unwrap()).unwrap();
        assert_eq!(req.strokes, strokes);
        assert_eq!(pending(&dir).len(), 2);
        atomic(
            &dir.join("outbox").join(format!("{a}.result.json")),
            br#"{"status":"complete"}"#,
        )
        .unwrap();
        assert_eq!(pending(&dir), vec![b]);
        assert_eq!(conversation(&dir).unwrap(), req.conversation_id);
        fs::remove_dir_all(dir).unwrap();
    }
}
