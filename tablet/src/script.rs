//! Tom Riddle's hand: shape and rasterize reply text, thin it to single-pixel
//! pen paths (Zhang-Suen), trace them into ordered strokes, and yield them for
//! stroke-by-stroke animation. Each supported script uses an embedded
//! handwriting face, with Noto fonts retained for uncommon glyphs.

use ab_glyph::{point, Font, FontRef, Glyph, GlyphId, PxScale, ScaleFont};
use rustybuzz::{shape, Face as ShapingFace, UnicodeBuffer};
use unicode_segmentation::UnicodeSegmentation;

const CAVEAT: &[u8] = include_bytes!("../fonts/Caveat.ttf");
const NOTO: &[u8] = include_bytes!("../fonts/NotoSans-Regular.ttf");

struct FontFace {
    raster: FontRef<'static>,
    shaper: ShapingFace<'static>,
}

/// Ordered font collection used by every text surface in the diary.
///
/// The first face that covers a whole grapheme wins. Handwriting faces precede
/// their Noto fallbacks, while whitespace and punctuation inherit a neighboring
/// face. Keeping graphemes together prevents fallback selection from splitting
/// Indic conjuncts before OpenType shaping.
pub struct FontBook {
    faces: Vec<FontFace>,
}

struct PositionedGlyph {
    face: usize,
    glyph: Glyph,
}

struct Layout {
    glyphs: Vec<PositionedGlyph>,
    advance: f32,
}

impl FontBook {
    pub fn new() -> Result<Self, String> {
        let sources = [("Caveat", CAVEAT), ("Noto Sans", NOTO)];
        let mut faces = Vec::with_capacity(sources.len());
        for (name, data) in sources {
            let raster =
                FontRef::try_from_slice(data).map_err(|e| format!("cannot load {name}: {e}"))?;
            let shaper = ShapingFace::from_slice(data, 0)
                .ok_or_else(|| format!("cannot shape with {name}"))?;
            faces.push(FontFace { raster, shaper });
        }
        Ok(Self { faces })
    }

    fn explicit_face(&self, grapheme: &str) -> Option<usize> {
        let chars: Vec<char> = grapheme
            .chars()
            .filter(|&c| {
                !c.is_whitespace()
                    && !matches!(c, '\u{200c}' | '\u{200d}' | '\u{fe0e}' | '\u{fe0f}')
            })
            .collect();
        if chars.is_empty() {
            return None;
        }

        let mut matches = self
            .faces
            .iter()
            .enumerate()
            .filter(|(_, face)| chars.iter().all(|&c| face.raster.glyph_id(c).0 != 0))
            .map(|(index, _)| index);
        let first = matches.next();
        if chars.iter().all(|c| !c.is_alphanumeric()) && first.is_some() && matches.next().is_some()
        {
            None
        } else {
            first
        }
    }

    fn runs(&self, text: &str) -> Vec<(usize, String)> {
        let graphemes: Vec<&str> = text.graphemes(true).collect();
        let explicit: Vec<Option<usize>> = graphemes
            .iter()
            .map(|grapheme| self.explicit_face(grapheme))
            .collect();
        let mut runs: Vec<(usize, String)> = Vec::new();
        let mut previous = None;

        for (i, grapheme) in graphemes.into_iter().enumerate() {
            let face = explicit[i]
                .or(previous)
                .or_else(|| explicit[i + 1..].iter().flatten().copied().next())
                .unwrap_or(0);
            previous = Some(face);
            if let Some((run_face, run)) = runs.last_mut() {
                if *run_face == face {
                    run.push_str(grapheme);
                    continue;
                }
            }
            runs.push((face, grapheme.to_string()));
        }
        runs
    }

    fn layout(&self, text: &str, px: f32) -> Layout {
        let scale = PxScale::from(px);
        let baseline = self
            .faces
            .iter()
            .map(|face| face.raster.as_scaled(scale).ascent())
            .fold(0.0f32, f32::max);
        let mut caret_x = 0.0f32;
        let mut glyphs = Vec::new();

        for (face_index, run) in self.runs(text) {
            let face = &self.faces[face_index];
            let scaled = face.raster.as_scaled(scale);
            let h_scale = scaled.h_scale_factor();
            let v_scale = scaled.v_scale_factor();
            let mut buffer = UnicodeBuffer::new();
            buffer.push_str(&run);
            buffer.guess_segment_properties();
            let shaped = shape(&face.shaper, &[], buffer);

            for (info, position) in shaped.glyph_infos().iter().zip(shaped.glyph_positions()) {
                let mut glyph = GlyphId(info.glyph_id as u16).with_scale(scale);
                glyph.position = point(
                    caret_x + position.x_offset as f32 * h_scale,
                    baseline - position.y_offset as f32 * v_scale,
                );
                glyphs.push(PositionedGlyph {
                    face: face_index,
                    glyph,
                });
                caret_x += position.x_advance as f32 * h_scale;
            }
        }

        Layout {
            glyphs,
            advance: caret_x,
        }
    }
}

pub struct Line {
    pub width: usize,
    pub height: usize,
    /// Bit mask of inked pixels, row-major.
    pub mask: Vec<bool>,
}

/// Rasterize one shaped line of text at `px` height into a boolean mask.
pub fn rasterize_line(fonts: &FontBook, text: &str, px: f32) -> Line {
    const SIDE_PAD: f32 = 4.0;
    const VERTICAL_PAD: f32 = 2.0;
    let layout = fonts.layout(text, px);
    let mut min_x = 0.0f32;
    let mut max_x = layout.advance.max(0.0);
    let mut min_y = 0.0f32;
    let mut max_y = px;

    for item in &layout.glyphs {
        if let Some(outline) = fonts.faces[item.face]
            .raster
            .outline_glyph(item.glyph.clone())
        {
            let bounds = outline.px_bounds();
            min_x = min_x.min(bounds.min.x);
            max_x = max_x.max(bounds.max.x);
            min_y = min_y.min(bounds.min.y);
            max_y = max_y.max(bounds.max.y);
        }
    }

    let x_shift = SIDE_PAD - min_x.floor();
    let y_shift = VERTICAL_PAD - min_y.floor();
    let width = ((max_x.ceil() - min_x.floor()) + SIDE_PAD * 2.0)
        .ceil()
        .max(1.0) as usize;
    let height = ((max_y.ceil() - min_y.floor()) + VERTICAL_PAD * 2.0)
        .ceil()
        .max(1.0) as usize;
    let mut mask = vec![false; width * height];
    for item in layout.glyphs {
        let mut glyph = item.glyph;
        glyph.position.x += x_shift;
        glyph.position.y += y_shift;
        if let Some(outline) = fonts.faces[item.face].raster.outline_glyph(glyph) {
            let bounds = outline.px_bounds();
            outline.draw(|x, y, cov| {
                if cov > 0.5 {
                    let px_x = bounds.min.x as i32 + x as i32;
                    let px_y = bounds.min.y as i32 + y as i32;
                    if px_x >= 0 && px_y >= 0 && (px_x as usize) < width && (px_y as usize) < height
                    {
                        mask[px_y as usize * width + px_x as usize] = true;
                    }
                }
            });
        }
    }
    Line {
        width,
        height,
        mask,
    }
}

/// Measure the advance width of text at `px` without rasterizing.
pub fn measure(fonts: &FontBook, text: &str, px: f32) -> f32 {
    fonts.layout(text, px).advance.max(0.0)
}

/// Zhang-Suen thinning: reduce the mask to 1px-wide skeleton lines.
pub fn thin(line: &mut Line) {
    let (w, h) = (line.width, line.height);
    let idx = |x: usize, y: usize| y * w + x;
    loop {
        let mut changed = false;
        for phase in 0..2 {
            let mut to_clear = Vec::new();
            for y in 1..h.saturating_sub(1) {
                for x in 1..w.saturating_sub(1) {
                    if !line.mask[idx(x, y)] {
                        continue;
                    }
                    let p = [
                        line.mask[idx(x, y - 1)],     // p2 N
                        line.mask[idx(x + 1, y - 1)], // p3 NE
                        line.mask[idx(x + 1, y)],     // p4 E
                        line.mask[idx(x + 1, y + 1)], // p5 SE
                        line.mask[idx(x, y + 1)],     // p6 S
                        line.mask[idx(x - 1, y + 1)], // p7 SW
                        line.mask[idx(x - 1, y)],     // p8 W
                        line.mask[idx(x - 1, y - 1)], // p9 NW
                    ];
                    let b: u32 = p.iter().map(|&v| v as u32).sum();
                    if !(2..=6).contains(&b) {
                        continue;
                    }
                    let mut a = 0;
                    for i in 0..8 {
                        if !p[i] && p[(i + 1) % 8] {
                            a += 1;
                        }
                    }
                    if a != 1 {
                        continue;
                    }
                    let (c1, c2) = if phase == 0 {
                        (!(p[0] && p[2] && p[4]), !(p[2] && p[4] && p[6]))
                    } else {
                        (!(p[0] && p[2] && p[6]), !(p[0] && p[4] && p[6]))
                    };
                    if c1 && c2 {
                        to_clear.push(idx(x, y));
                    }
                }
            }
            if !to_clear.is_empty() {
                changed = true;
                for i in to_clear {
                    line.mask[i] = false;
                }
            }
        }
        if !changed {
            break;
        }
    }
}

/// Trace the skeleton into polyline strokes, ordered left-to-right so the
/// animation writes like a hand.
pub fn trace(line: &Line) -> Vec<Vec<(i32, i32)>> {
    let (w, h) = (line.width, line.height);
    let at = |x: i32, y: i32| -> bool {
        x >= 0
            && y >= 0
            && (x as usize) < w
            && (y as usize) < h
            && line.mask[y as usize * w + x as usize]
    };
    let neighbors = |x: i32, y: i32| -> Vec<(i32, i32)> {
        let mut out = Vec::new();
        for dy in -1..=1i32 {
            for dx in -1..=1i32 {
                if (dx != 0 || dy != 0) && at(x + dx, y + dy) {
                    out.push((x + dx, y + dy));
                }
            }
        }
        out
    };

    let mut visited = vec![false; w * h];
    let vis = |v: &mut Vec<bool>, x: i32, y: i32| {
        v[y as usize * w + x as usize] = true;
    };
    let seen = |v: &Vec<bool>, x: i32, y: i32| -> bool { v[y as usize * w + x as usize] };

    // Endpoints first (degree 1), then any remaining pixels (loops).
    let mut starts: Vec<(i32, i32)> = Vec::new();
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            if at(x, y) && neighbors(x, y).len() == 1 {
                starts.push((x, y));
            }
        }
    }
    for y in 0..h as i32 {
        for x in 0..w as i32 {
            if at(x, y) {
                starts.push((x, y));
            }
        }
    }

    let mut strokes: Vec<Vec<(i32, i32)>> = Vec::new();
    for (sx, sy) in starts {
        if seen(&visited, sx, sy) {
            continue;
        }
        let mut path = vec![(sx, sy)];
        vis(&mut visited, sx, sy);
        let (mut cx, mut cy) = (sx, sy);
        loop {
            let next = neighbors(cx, cy)
                .into_iter()
                .find(|&(nx, ny)| !seen(&visited, nx, ny));
            match next {
                Some((nx, ny)) => {
                    vis(&mut visited, nx, ny);
                    path.push((nx, ny));
                    cx = nx;
                    cy = ny;
                }
                None => break,
            }
        }
        // Punctuation dots and the dots over i/j can thin to one or two pixels.
        // The writer can stamp a single-point stroke, so preserve every
        // connected component produced by the font rasterizer.
        if !path.is_empty() {
            strokes.push(path);
        }
    }
    strokes.sort_by_key(|s| s.iter().map(|&(x, _)| x).min().unwrap_or(0));
    strokes
}

/// Word-wrap `text` to lines that fit `max_px` at scale `px`. Oversized words
/// are split at Unicode grapheme boundaries, which also lets Chinese text wrap
/// without spaces and keeps Indic conjuncts together.
pub fn wrap(fonts: &FontBook, text: &str, px: f32, max_px: f32) -> Vec<String> {
    let mut lines = Vec::new();
    for para in text.lines() {
        let mut cur = String::new();
        for word in para.split_whitespace() {
            let cand = if cur.is_empty() {
                word.to_string()
            } else {
                format!("{cur} {word}")
            };
            if measure(fonts, &cand, px) <= max_px {
                cur = cand;
            } else {
                if !cur.is_empty() {
                    lines.push(std::mem::take(&mut cur));
                }
                if measure(fonts, word, px) <= max_px {
                    cur = word.to_string();
                    continue;
                }
                for grapheme in UnicodeSegmentation::graphemes(word, true) {
                    let mut candidate = cur.clone();
                    candidate.push_str(grapheme);
                    if !cur.is_empty() && measure(fonts, &candidate, px) > max_px {
                        lines.push(std::mem::take(&mut cur));
                    }
                    cur.push_str(grapheme);
                }
            }
        }
        if !cur.is_empty() {
            lines.push(cur);
        }
    }
    lines
}
