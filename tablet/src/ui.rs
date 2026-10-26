use crate::{
    fb::{BBox, SCREEN_H, SCREEN_W},
    script::{self, FontBook},
    surface::{Surface, BLACK, WHITE},
};

pub const MARGIN: usize = 70;
pub const TOP: usize = 170;
pub const PX: f32 = 62.0;
pub const LINE_H: usize = 86;

pub fn draw_text(surf: &mut Surface, font: &FontBook, text: &str, x: usize, y: usize, px: f32) {
    let line = script::rasterize_line(font, text, px);
    for row in 0..line.height {
        for col in 0..line.width {
            if line.mask[row * line.width + col] {
                surf.put_px((x + col) as i32, (y + row) as i32, BLACK);
            }
        }
    }
}

pub fn header(surf: &mut Surface, font: &FontBook, eraser: bool) {
    surf.fill_rect(0, 0, SCREEN_W, 125, WHITE);
    for (index, label) in [
        "Писать",
        "Назад",
        "Вперёд",
        if eraser { "Перо" } else { "Ластик" },
        "Закрыть",
    ]
    .iter()
    .enumerate()
    {
        draw_text(surf, font, label, index * (SCREEN_W / 5) + 25, 30, 40.0);
    }
    surf.brush_line(20, 122, SCREEN_W as i32 - 20, 122, 1, BLACK);
}

pub fn footer(surf: &mut Surface, font: &FontBook, text: &str) {
    surf.fill_rect(0, SCREEN_H - 100, SCREEN_W, 100, WHITE);
    draw_text(surf, font, text, 35, SCREEN_H - 75, 35.0);
}

pub fn pages(font: &FontBook, text: &str) -> Vec<Vec<String>> {
    let lines = script::wrap(font, text, PX, (SCREEN_W - 2 * MARGIN) as f32);
    let capacity = (SCREEN_H - TOP - 150) / LINE_H;
    let mut pages: Vec<_> = lines.chunks(capacity).map(|lines| lines.to_vec()).collect();
    if pages.is_empty() {
        pages.push(Vec::new());
    }
    pages
}

pub fn strokes(font: &FontBook, lines: &[String]) -> Vec<Vec<(i32, i32)>> {
    let mut all = Vec::new();
    for (index, text) in lines.iter().enumerate() {
        let mut line = script::rasterize_line(font, text, PX);
        script::thin(&mut line);
        for stroke in script::trace(&line) {
            all.push(
                stroke
                    .iter()
                    .map(|&(x, y)| (x + MARGIN as i32, y + (TOP + index * LINE_H) as i32))
                    .collect(),
            );
        }
    }
    all
}

pub fn draw_stroke(surf: &mut Surface, stroke: &[(i32, i32)]) -> BBox {
    let mut bbox = BBox::empty();
    for (i, &(x, y)) in stroke.iter().enumerate() {
        if i > 0 {
            let (px, py) = stroke[i - 1];
            surf.brush_line(px, py, x, y, 2, BLACK);
        } else {
            surf.stamp(x, y, 2, BLACK);
        }
        bbox.add(x, y, 3);
    }
    bbox
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn russian_reply_is_shaped_and_paginated_without_clipping() {
        let font = FontBook::new().unwrap();
        let pages = pages(
            &font,
            &"Я помню ваш вопрос. Ответ найден в книге [1]. ".repeat(100),
        );
        assert!(pages.len() > 1);
        let lines = script::wrap(&font, "Привет, мир!", PX, 1200.0);
        let strokes = strokes(&font, &lines);
        assert!(strokes.iter().map(Vec::len).sum::<usize>() > 100);
        for page in pages {
            for stroke in strokes_for_page(&font, &page) {
                for (x, y) in stroke {
                    assert!(x >= 0 && x < SCREEN_W as i32 && y < (SCREEN_H - 100) as i32);
                }
            }
        }
    }
    fn strokes_for_page(font: &FontBook, lines: &[String]) -> Vec<Vec<(i32, i32)>> {
        strokes(font, lines)
    }
}
