#![allow(dead_code)] // The vendored framebuffer framework exposes more than this app needs.
mod client;
mod fb;
mod ink;
mod qtfb;
mod script;
mod snapshot;
mod surface;
mod ui;

use fb::{SCREEN_H, SCREEN_W};
use std::fs;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Arc,
};
use std::time::{Duration, Instant};
use surface::{PixFmt, Surface, WHITE};

enum Mode {
    Writing,
    Waiting(String),
    Answer { id: String, page: usize },
}

fn answer_text(value: &serde_json::Value) -> String {
    let question = value["transcription"].as_str().unwrap_or("");
    let answer = value["answer"]
        .as_str()
        .or(value["error"].as_str())
        .unwrap_or("Ожидаю ответ…");
    let mut text = format!("{question}\n\n{answer}");
    if let Some(citations) = value["citations"].as_array() {
        for citation in citations {
            text.push_str(&format!(
                "\n[{}] {} — {}",
                citation["number"],
                citation["title"].as_str().unwrap_or(""),
                citation["location"].as_str().unwrap_or("")
            ));
            if let Some(url) = citation["url"].as_str() {
                text.push_str(&format!("\n{url}"));
            }
        }
    }
    text
}

fn render(text: &str, output: &str) -> std::io::Result<()> {
    let mut bytes = vec![255; SCREEN_W * SCREEN_H * 2];
    let mut surf = Surface::new(
        bytes.as_mut_ptr(),
        bytes.len(),
        SCREEN_W,
        SCREEN_H,
        SCREEN_W * 2,
        PixFmt::Rgb565,
    );
    let font = script::FontBook::new().map_err(std::io::Error::other)?;
    ui::header(&mut surf, &font, false);
    let pages = ui::pages(&font, text);
    for stroke in ui::strokes(&font, &pages[0]) {
        ui::draw_stroke(&mut surf, &stroke);
    }
    ui::footer(&mut surf, &font, "Живой дневник · 1 / 1");
    let pixels: Vec<u8> = (0..SCREEN_H)
        .flat_map(|y| {
            let s = &surf;
            (0..SCREEN_W).map(move |x| s.luma(x as i32, y as i32))
        })
        .collect();
    let file = fs::File::create(output)?;
    let mut encoder = png::Encoder::new(file, SCREEN_W as u32, SCREEN_H as u32);
    encoder.set_color(png::ColorType::Grayscale);
    encoder.write_header()?.write_image_data(&pixels)?;
    Ok(())
}

fn main() -> std::io::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--snapshot") {
        let output = args
            .get(2)
            .ok_or_else(|| std::io::Error::other("missing output image"))?;
        return client::atomic(std::path::Path::new(output), &snapshot::capture()?);
    }
    if args.get(1).map(String::as_str) == Some("--render") {
        return render(
            args.get(2)
                .map(String::as_str)
                .unwrap_or("Привет. Я ваш живой дневник."),
            args.get(3).map(String::as_str).unwrap_or("diary.png"),
        );
    }
    let dir = client::root();
    fs::create_dir_all(dir.join("outbox"))?;
    if args.get(1).map(String::as_str) == Some("--capture-screen") {
        let output = args
            .get(2)
            .ok_or_else(|| std::io::Error::other("missing result pointer"))?;
        let result =
            snapshot::capture().and_then(|png| client::enqueue_mode(&dir, &png, Vec::new(), true));
        let pointer = match result {
            Ok(id) => serde_json::json!({"id":id}),
            Err(error) => {
                eprintln!("Screen capture failed: {error}");
                serde_json::json!({"error":"Не удалось прочитать страницу. Закройте панель и повторите."})
            }
        };
        return client::atomic(std::path::Path::new(output), &serde_json::to_vec(&pointer)?);
    }
    if args.get(1).map(String::as_str) == Some("--capture-note") {
        let png = fs::read(
            args.get(2)
                .ok_or_else(|| std::io::Error::other("missing capture"))?,
        )?;
        let id = client::enqueue_mode(&dir, &png, Vec::new(), true)?;
        let output = args
            .get(3)
            .ok_or_else(|| std::io::Error::other("missing result pointer"))?;
        client::atomic(
            std::path::Path::new(output),
            &serde_json::to_vec(&serde_json::json!({"id":id}))?,
        )?;
        return Ok(());
    }
    let stop = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGTERM, stop.clone())?;
    signal_hook::flag::register(signal_hook::consts::SIGINT, stop.clone())?;
    if args.get(1).map(String::as_str) == Some("--sync-only") {
        client::sync_loop(dir, stop);
        return Ok(());
    }
    let key = std::env::var("QTFB_KEY")
        .map_err(|_| std::io::Error::other("Start the diary from AppLoad (QTFB_KEY missing)"))?
        .parse::<i32>()
        .map_err(std::io::Error::other)?;
    let mut display = qtfb::QtfbClient::connect(key, qtfb::FBFMT_RM2FB, SCREEN_W, SCREEN_H, 2)?;
    let buffer = display.framebuffer();
    let mut surf = Surface::new(
        buffer.as_mut_ptr(),
        buffer.len(),
        SCREEN_W,
        SCREEN_H,
        SCREEN_W * 2,
        PixFmt::Rgb565,
    );
    let font = script::FontBook::new().map_err(std::io::Error::other)?;
    let sync_dir = dir.clone();
    let sync_stop = stop.clone();
    std::thread::spawn(move || client::sync_loop(sync_dir, sync_stop));
    let mut ink = ink::Ink::new();
    surf.fill_rect(0, 0, SCREEN_W, SCREEN_H, WHITE);
    for stroke in client::draft(&dir) {
        for (x, y, radius) in stroke {
            ink.pen_point(&mut surf, x, y, radius);
        }
        ink.pen_up();
    }
    ui::header(&mut surf, &font, false);
    ui::footer(&mut surf, &font, "Напишите вопрос. После паузы я отвечу.");
    display.update_all()?;
    let mut mode = Mode::Writing;
    if ink.is_empty() {
        if let Some(id) = client::pending(&dir).first() {
            mode = Mode::Waiting(id.clone());
            ui::footer(&mut surf, &font, "Вопрос сохранён. Ожидаю ответ…");
            display.update_all()?;
        }
    }
    let mut last_pen = None;
    let drafts = client::DraftWriter::new(dir.clone());
    let mut dirty_ink = fb::BBox::empty();
    let mut last_paint = Instant::now();
    let mut down = false;
    let mut control = false;
    let mut eraser = false;
    let mut animation: Vec<Vec<(i32, i32)>> = Vec::new();
    let mut animation_index = 0;
    let mut last_poll = Instant::now();
    let mut live_lines: Vec<String> = Vec::new();
    let mut live_started = false;
    let mut live_text = String::new();
    let idle = std::env::var("REMARKABL_IDLE_MS")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(5000)
        .clamp(500, 30000);
    while !stop.load(Ordering::Relaxed) {
        for event in display.drain_events()? {
            if event.input_type == qtfb::INPUT_PEN_RELEASE {
                down = false;
                control = false;
                ink.pen_up();
                if matches!(mode, Mode::Writing) {
                    drafts.save(ink.stroke_list().to_vec())?;
                    last_pen = Some(Instant::now());
                }
                continue;
            }
            if !matches!(
                event.input_type,
                qtfb::INPUT_PEN_PRESS | qtfb::INPUT_PEN_UPDATE
            ) {
                continue;
            }
            let pressed = event.input_type == qtfb::INPUT_PEN_PRESS || !down;
            down = true;
            if pressed && event.y < 125 {
                control = true;
                let button = event.x.max(0) as usize / (SCREEN_W / 5);
                match button {
                    0 if ink.is_empty() => {
                        mode = Mode::Writing;
                        animation.clear();
                        last_pen = None;
                        surf.fill_rect(0, 125, SCREEN_W, SCREEN_H - 125, WHITE);
                        ui::footer(&mut surf, &font, "Напишите следующий вопрос.");
                    }
                    1 | 2 if ink.is_empty() => {
                        let history: Vec<_> = client::requests(&dir)
                            .iter()
                            .map(|p| client::id_for(p))
                            .filter(|id| {
                                client::result(&dir, id).is_some_and(|v| {
                                    v["status"] == "complete" || v["status"] == "error"
                                })
                            })
                            .collect();
                        let mut chosen = None;
                        if let Mode::Answer { ref id, page } = mode {
                            let pages =
                                ui::pages(&font, &answer_text(&client::result(&dir, id).unwrap()));
                            if button == 1 && page > 0 {
                                chosen = Some((id.clone(), page - 1));
                            }
                            if button == 2 && page + 1 < pages.len() {
                                chosen = Some((id.clone(), page + 1));
                            }
                            if chosen.is_none() {
                                if let Some(index) = history.iter().position(|item| item == id) {
                                    let next = if button == 1 {
                                        index.checked_sub(1)
                                    } else {
                                        Some(index + 1)
                                    };
                                    chosen =
                                        next.and_then(|i| history.get(i)).map(|id| (id.clone(), 0));
                                }
                            }
                        } else {
                            chosen = history.last().map(|id| (id.clone(), 0));
                        }
                        if let Some((id, page)) = chosen {
                            let value = client::result(&dir, &id).unwrap();
                            let pages = ui::pages(&font, &answer_text(&value));
                            animation = ui::strokes(&font, &pages[page]);
                            animation_index = 0;
                            surf.fill_rect(0, 125, SCREEN_W, SCREEN_H - 125, WHITE);
                            ui::footer(
                                &mut surf,
                                &font,
                                &format!("Страница {} / {}", page + 1, pages.len()),
                            );
                            mode = Mode::Answer { id, page };
                        }
                    }
                    3 => {
                        if let Mode::Answer { ref id, .. } = mode {
                            if client::result(&dir, id).is_some_and(|v| v["status"] == "error") {
                                client::retry(&dir, id)?;
                                mode = Mode::Waiting(id.clone());
                            }
                        } else {
                            eraser = !eraser;
                        }
                    }
                    4 => stop.store(true, Ordering::Relaxed),
                    _ => {}
                }
                ui::header(&mut surf, &font, eraser);
                display.update_all()?;
                continue;
            }
            if control || event.y < ui::TOP as i32 || event.y > (SCREEN_H - 110) as i32 {
                continue;
            }
            if matches!(mode, Mode::Writing) {
                last_pen = None;
                let bbox = if eraser {
                    ink.erase_point(&mut surf, event.x, event.y, 18)
                } else {
                    ink.pen_point(&mut surf, event.x, event.y, 2 + event.d.clamp(0, 100) / 45)
                };
                dirty_ink.merge(bbox);
            }
        }
        if !dirty_ink.is_empty() && (!down || last_paint.elapsed() >= Duration::from_millis(16)) {
            let (x, y, w, h) = dirty_ink.rect();
            display.update_partial(x, y, w, h)?;
            dirty_ink = fb::BBox::empty();
            last_paint = Instant::now();
        }
        if matches!(mode, Mode::Writing)
            && !down
            && !ink.is_empty()
            && last_pen.is_some_and(|t: Instant| t.elapsed().as_millis() >= idle as u128)
        {
            let path = dir.join("current.png");
            ink.to_png(&surf, path.to_str().unwrap())?;
            drafts.flush()?;
            let id = client::enqueue(&dir, &fs::read(&path)?, ink.stroke_list().to_vec())?;
            let region = ink.bbox;
            for stage in 0..3 {
                ink::dissolve_pass(&mut surf, region, stage, 3);
                let (x, y, w, h) = region.rect();
                display.update_partial(x, y, w, h)?;
                std::thread::sleep(Duration::from_millis(30));
            }
            ink.clear();
            last_pen = None;
            animation.clear();
            animation_index = 0;
            live_lines.clear();
            live_started = false;
            live_text.clear();
            mode = Mode::Waiting(id);
            ui::footer(&mut surf, &font, "Вопрос сохранён. Ожидаю ответ…");
            display.update_all()?;
        }
        if last_poll.elapsed() >= Duration::from_millis(100) {
            last_poll = Instant::now();
            if let Mode::Waiting(ref id) = mode {
                let id = id.clone();
                if let Some(value) = client::result(&dir, &id) {
                    let done = value["status"] == "complete" || value["status"] == "error";
                    let text = answer_text(&value);
                    if done
                        || (text != live_text
                            && value["answer"].as_str().is_some_and(|s| !s.is_empty()))
                    {
                        live_text = text.clone();
                        let pages = ui::pages(&font, &text);
                        let lines = &pages[0];
                        // A partial final line can still reflow. Commit complete lines only.
                        let count = if done || pages.len() > 1 {
                            lines.len()
                        } else {
                            lines.len().saturating_sub(1)
                        };
                        let changed = live_lines.len() > count
                            || live_lines.iter().zip(lines).any(|(a, b)| a != b);
                        if !live_started || changed {
                            surf.fill_rect(0, 125, SCREEN_W, SCREEN_H - 125, WHITE);
                            animation.clear();
                            animation_index = 0;
                            live_lines.clear();
                            live_started = true;
                            display.update_all()?;
                        }
                        for (index, text) in
                            lines.iter().enumerate().take(count).skip(live_lines.len())
                        {
                            let mut strokes = ui::strokes(&font, std::slice::from_ref(text));
                            for stroke in &mut strokes {
                                for point in stroke {
                                    point.1 += (index * ui::LINE_H) as i32;
                                }
                            }
                            animation.extend(strokes);
                            live_lines.push(text.clone());
                        }
                        let message = if value["status"] == "error" {
                            "Ответ не получен. Нажмите Ластик для повтора.".to_owned()
                        } else if done {
                            format!("Ответ сохранён · страница 1 / {}", pages.len())
                        } else {
                            "Ответ появляется по мере генерации…".to_owned()
                        };
                        ui::footer(&mut surf, &font, &message);
                        display.update_partial(0, (SCREEN_H - 100) as i32, SCREEN_W as i32, 100)?;
                        if done {
                            mode = Mode::Answer { id, page: 0 };
                        }
                    }
                } else if dir.join("network-error").exists() {
                    ui::footer(
                        &mut surf,
                        &font,
                        "Нет сети. Вопрос сохранён до подключения.",
                    );
                    display.update_partial(0, (SCREEN_H - 100) as i32, SCREEN_W as i32, 100)?;
                }
            }
        }
        if animation_index < animation.len() {
            let mut bbox = fb::BBox::empty();
            let frame_start = Instant::now();
            for _ in 0..20 {
                if let Some(stroke) = animation.get(animation_index) {
                    let dirty = ui::draw_stroke(&mut surf, stroke);
                    bbox.add(dirty.x0, dirty.y0, 3);
                    bbox.add(dirty.x1, dirty.y1, 3);
                    animation_index += 1;
                    if frame_start.elapsed() >= Duration::from_millis(4) {
                        break;
                    }
                }
            }
            if !bbox.is_empty() {
                let (x, y, w, h) = bbox.rect();
                display.update_partial(x, y, w, h)?;
            }
        }
        std::thread::sleep(Duration::from_millis(if down { 3 } else { 12 }));
    }
    ink.pen_up();
    if matches!(mode, Mode::Writing) {
        drafts.save(ink.stroke_list().to_vec())?;
    }
    drafts.flush()?;
    display.terminate();
    Ok(())
}
