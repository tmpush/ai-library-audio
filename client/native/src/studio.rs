use crate::{
    audio::{self, Player, Recording},
    backend::{self, Api, Engine},
};
use eframe::egui::{self, Color32, FontFamily, FontId, RichText, Stroke, Vec2};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::PathBuf,
    sync::mpsc::{self, Receiver, Sender},
    time::{Duration, Instant},
};

fn s<'a>(v: &'a Value, key: &str) -> &'a str {
    v[key].as_str().unwrap_or("")
}
fn list(v: &Value) -> Vec<Value> {
    v.as_array().cloned().unwrap_or_default()
}
fn duration(seconds: f64) -> String {
    let n = seconds as u64;
    if n >= 3600 {
        format!("{}:{:02}:{:02}", n / 3600, n / 60 % 60, n % 60)
    } else {
        format!("{}:{:02}", n / 60, n % 60)
    }
}
fn serif(size: f32) -> FontId {
    FontId::new(size, FontFamily::Name("Lora".into()))
}
fn title(ui: &mut egui::Ui, text: impl Into<String>, size: f32) {
    ui.label(RichText::new(text).font(serif(size)));
}
const VOICE_GROUPS: [(&str, &str); 3] = [
    ("ru", "Русские дикторы"),
    ("en", "Английские дикторы"),
    ("personal", "Мои голоса"),
];

fn voice_group(voice: &Value) -> &str {
    if voice["bundled"] == true { s(voice, "language") } else { "personal" }
}

fn status(job: &Value) -> &str {
    match s(job, "status") {
        "done" => "Готово",
        "running" => "Озвучивается",
        "queued" => "В очереди",
        "paused" => "На паузе",
        "failed" => "Нужна проверка",
        _ => "Можно озвучивать",
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Page {
    Home,
    Studio,
    Voices,
    Exports,
    Settings,
}
#[derive(Serialize, Deserialize)]
#[serde(default)]
struct Preferences {
    dark: bool,
    book: String,
    voices: BTreeMap<String, String>,
    default_voice: String,
    export_dir: String,
    denoise: bool,
    exports: Vec<String>,
}
impl Default for Preferences {
    fn default() -> Self {
        Self {
            dark: true,
            book: String::new(),
            voices: BTreeMap::new(),
            default_voice: String::new(),
            export_dir: String::new(),
            denoise: true,
            exports: vec![],
        }
    }
}
#[derive(Clone)]
enum Done {
    Book(String),
    UpdatedBook,
    EditedBook,
    Imported,
    Render(bool),
    Voice,
    Prepared,
    Play(String),
    Saved(String),
    Changed,
    Deleted,
    Catalog,
    Download,
    Hardware,
    Repair,
    AppliedTake,
    Rules,
    RulesSaved,
    RepairText,
}
enum Event {
    Ready(Result<(Api, Engine), String>),
    Snapshot(Result<Value, String>),
    Action(Done, Result<Value, String>),
}

#[derive(Clone, Copy)]
struct Palette {
    bg: Color32,
    surface: Color32,
    hover: Color32,
    text: Color32,
    muted: Color32,
    border: Color32,
    accent: Color32,
    ink: Color32,
    soft: Color32,
}
fn c(hex: u32) -> Color32 {
    Color32::from_rgb((hex >> 16) as u8, (hex >> 8) as u8, hex as u8)
}
impl Palette {
    fn new(dark: bool) -> Self {
        if dark {
            Self {
                bg: c(0x202023),
                surface: c(0x29292d),
                hover: c(0x34343b),
                text: c(0xf4f3f6),
                muted: c(0xb6b4bf),
                border: c(0x44444d),
                accent: c(0xc7aff0),
                ink: c(0x30213f),
                soft: c(0x393343),
            }
        } else {
            Self {
                bg: c(0xf6f6f8),
                surface: c(0xffffff),
                hover: c(0xeeeaf5),
                text: c(0x25242b),
                muted: c(0x64616f),
                border: c(0xd7d5df),
                accent: c(0xc7aff0),
                ink: c(0x30213f),
                soft: c(0xeee8f7),
            }
        }
    }
}
fn panel(p: Palette) -> egui::Frame {
    egui::Frame::new()
        .fill(p.surface)
        .stroke(Stroke::new(1.0_f32, p.border))
        .corner_radius(10)
        .inner_margin(24)
}
fn primary(ui: &mut egui::Ui, p: Palette, text: &str) -> egui::Response {
    ui.add_sized(
        [ui.available_width(), 44.0],
        egui::Button::new(RichText::new(text).color(p.ink).size(16.0)).fill(p.accent),
    )
}
fn secondary(ui: &mut egui::Ui, text: &str) -> egui::Response {
    ui.add_sized([ui.available_width(), 40.0], egui::Button::new(text))
}
fn hint(ui: &mut egui::Ui, p: Palette, text: impl Into<String>) {
    ui.label(RichText::new(text).color(p.muted).size(13.0));
}

pub struct StudioApp {
    prefs: Preferences,
    palette: Palette,
    page: Page,
    api: Option<Api>,
    engine: Option<Engine>,
    tx: Sender<Event>,
    rx: Receiver<Event>,
    snapshot: Value,
    polling: bool,
    last_poll: Instant,
    busy: String,
    notice: Option<(String, bool)>,
    started: Instant,
    book: Value,
    chapter: usize,
    search: String,
    chapter_only: bool,
    voice: String,
    numbers: bool,
    auto_stress: bool,
    remove_notes: bool,
    remove_furniture: bool,
    dictionary: String,
    edit: bool,
    blocks: Vec<String>,
    dirty: BTreeSet<usize>,
    metadata_title: String,
    metadata_author: String,
    prepared: Option<String>,
    repair: Option<Value>,
    repair_position: usize,
    repair_text: String,
    repair_search: String,
    rules: Option<String>,
    delete_confirm: bool,
    split_title: String,
    split_at: usize,
    preview_job: Option<String>,
    preview_wait: bool,
    player: Option<Player>,
    recording: Option<Recording>,
    recorded: Option<Vec<u8>>,
    microphones: Vec<String>,
    microphone: String,
    voice_name: String,
    transcript: String,
    license_key: String,
    model_dir: String,
    espeech_dir: String,
    device: String,
    backend: String,
    catalog: Option<Value>,
    download: Value,
    hardware: Value,
    exported: Vec<String>,
    brand_icon: egui::TextureHandle,
    covers: BTreeMap<String, egui::TextureHandle>,
    failed_covers: BTreeSet<String>,
    launch_failed: bool,
}
impl StudioApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let prefs: Preferences = std::fs::read(backend::data_dir().join("native-ui.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or_default();
        let palette = Palette::new(prefs.dark);
        let exported = prefs.exports.clone();
        let mut fonts = egui::FontDefinitions::default();
        fonts.font_data.insert(
            "Golos".into(),
            egui::FontData::from_static(include_bytes!("../assets/GolosText.ttf")).into(),
        );
        fonts.font_data.insert(
            "Lora".into(),
            egui::FontData::from_static(include_bytes!("../assets/Lora.ttf")).into(),
        );
        fonts
            .families
            .get_mut(&FontFamily::Proportional)
            .unwrap()
            .insert(0, "Golos".into());
        fonts.families.insert(
            FontFamily::Name("Lora".into()),
            vec!["Lora".into(), "Golos".into()],
        );
        cc.egui_ctx.set_fonts(fonts);
        Self::theme(&cc.egui_ctx, palette, prefs.dark);
        #[cfg(target_os = "windows")]
        crate::taskbar::set_caption(cc, palette.bg, palette.text);
        let (tx, rx) = mpsc::channel();
        let sender = tx.clone();
        let ctx = cc.egui_ctx.clone();
        std::thread::spawn(move || {
            let _ = sender.send(Event::Ready(Api::start()));
            ctx.request_repaint();
        });
        let brand = image::load_from_memory(include_bytes!("../assets/128x128.png")).expect("Lunatica icon").to_rgba8();
        let brand_icon = cc.egui_ctx.load_texture("lunatica-brand", egui::ColorImage::from_rgba_unmultiplied([brand.width() as usize, brand.height() as usize], brand.as_raw()), egui::TextureOptions::LINEAR);
        Self {
            brand_icon,
            prefs,
            palette,
            page: Page::Home,
            api: None,
            engine: None,
            tx,
            rx,
            snapshot: json!({}),
            polling: false,
            last_poll: Instant::now() - Duration::from_secs(5),
            busy: String::new(),
            notice: None,
            started: Instant::now(),
            book: Value::Null,
            chapter: 0,
            search: String::new(),
            chapter_only: true,
            voice: String::new(),
            numbers: true,
            auto_stress: false,
            remove_notes: false,
            remove_furniture: false,
            dictionary: String::new(),
            edit: false,
            blocks: vec![],
            dirty: BTreeSet::new(),
            metadata_title: String::new(),
            metadata_author: String::new(),
            prepared: None,
            repair: None,
            repair_position: 0,
            repair_text: String::new(),
            repair_search: String::new(),
            rules: None,
            delete_confirm: false,
            split_title: "Новая глава".into(),
            split_at: 1,
            preview_job: None,
            preview_wait: false,
            player: None,
            recording: None,
            recorded: None,
            microphones: vec![],
            microphone: String::new(),
            voice_name: "Мой голос".into(),
            transcript: audio::SCRIPT.into(),
            license_key: String::new(),
            model_dir: String::new(),
            espeech_dir: String::new(),
            device: if cfg!(target_os="windows") {"cuda:0"} else {"cpu"}.into(),
            backend: "auto".into(),
            catalog: None,
            download: Value::Null,
            hardware: Value::Null,
            exported,
            covers: BTreeMap::new(),
            failed_covers: BTreeSet::new(),
            launch_failed: false,
        }
    }
    fn theme(ctx: &egui::Context, p: Palette, dark: bool) {
        let mut visuals = if dark {
            egui::Visuals::dark()
        } else {
            egui::Visuals::light()
        };
        visuals.panel_fill = p.bg;
        visuals.window_fill = p.surface;
        visuals.extreme_bg_color = p.bg;
        visuals.faint_bg_color = p.soft;
        visuals.override_text_color = Some(p.text);
        visuals.selection.bg_fill = p.soft;
        visuals.selection.stroke = Stroke::new(1.0_f32, p.accent);
        visuals.widgets.noninteractive.fg_stroke = Stroke::new(1.0_f32, p.text);
        visuals.widgets.noninteractive.bg_stroke = Stroke::new(1.0_f32, p.border);
        visuals.widgets.inactive.bg_fill = p.surface;
        visuals.widgets.inactive.weak_bg_fill = p.surface;
        visuals.widgets.inactive.bg_stroke = Stroke::new(1.0_f32, p.border);
        visuals.widgets.hovered.bg_fill = p.hover;
        visuals.widgets.hovered.weak_bg_fill = p.hover;
        visuals.widgets.hovered.bg_stroke = Stroke::new(1.5_f32, p.accent);
        visuals.widgets.active.bg_fill = p.soft;
        visuals.widgets.active.weak_bg_fill = p.soft;
        visuals.widgets.active.bg_stroke = Stroke::new(1.5_f32, p.accent);
        visuals.hyperlink_color = if dark { p.accent } else { c(0x65438b) };
        ctx.set_visuals(visuals);
        ctx.style_mut(|style| {
            style.spacing.text_edit_width = 400.0;
            style.spacing.item_spacing = Vec2::new(12.0, 12.0);
            style.spacing.combo_width = 160.0;
            style.spacing.button_padding = Vec2::new(16.0, 10.0);
            style.spacing.interact_size = Vec2::new(40.0, 40.0);
            style.visuals.widgets.inactive.corner_radius = egui::CornerRadius::same(6);
            style.visuals.widgets.hovered.corner_radius = egui::CornerRadius::same(6);
            style.visuals.widgets.active.corner_radius = egui::CornerRadius::same(6);
            style
                .text_styles
                .insert(egui::TextStyle::Body, FontId::proportional(16.0));
            style
                .text_styles
                .insert(egui::TextStyle::Button, FontId::proportional(15.0));
            style
                .text_styles
                .insert(egui::TextStyle::Small, FontId::proportional(13.0));
            style
                .text_styles
                .insert(egui::TextStyle::Heading, serif(28.0));
        });
    }
    fn persist(&self) {
        let path = backend::data_dir().join("native-ui.json");
        let mut value = serde_json::to_value(&self.prefs).unwrap_or_default();
        value["exports"] = json!(self.exported);
        if let Ok(bytes) = serde_json::to_vec_pretty(&value) {
            let temp = path.with_extension("tmp");
            if std::fs::write(&temp, bytes).is_ok() {
                let _ = std::fs::rename(temp, path);
            }
        }
    }
    fn task<F>(&mut self, label: &str, done: Done, work: F)
    where
        F: FnOnce(Api) -> Result<Value, String> + Send + 'static,
    {
        if !self.dirty.is_empty()
            && matches!(done, Done::UpdatedBook | Done::Imported | Done::Book(_))
        {
            self.notice = Some((
                "Сначала сохраните или отмените правки текста в студии".into(),
                true,
            ));
            self.page = Page::Studio;
            return;
        }
        if !self.busy.is_empty() {
            return;
        }
        if let Some(api) = self.api.clone() {
            self.busy = label.into();
            let tx = self.tx.clone();
            std::thread::spawn(move || {
                let _ = tx.send(Event::Action(done, work(api)));
            });
        }
    }
    fn post(&mut self, path: String, body: Value, label: &str, done: Done) {
        self.task(label, done, move |api| api.post(&path, &body));
    }
    fn open_book(&mut self, id: String) {
        if !self.dirty.is_empty() {
            self.notice = Some((
                "Сначала сохраните изменения текста или отмените их в студии".into(),
                true,
            ));
            self.page = Page::Studio;
            return;
        }
        let route = format!("/projects/{id}");
        self.task(
            "Открываю книгу…",
            Done::Book(id),
            move |api| api.get(&route),
        );
    }
    fn read_book(&mut self, book: Value) {
        self.blocks = list(&book["blocks"])
            .iter()
            .enumerate()
            .map(|(i, v)| {
                book["spoken_overrides"][i.to_string()]
                    .as_str()
                    .unwrap_or(s(v, "text"))
                    .to_owned()
            })
            .collect();
        self.metadata_title = s(&book, "title").into();
        self.metadata_author = s(&book, "author").into();
        self.auto_stress = false;
        self.numbers = book["preparation"]["numbers"].as_bool().unwrap_or(true);
        self.remove_notes = book["preparation"]["remove_notes"]
            .as_bool()
            .unwrap_or(false);
        self.remove_furniture = book["preparation"]["remove_page_furniture"]
            .as_bool()
            .unwrap_or(false);
        self.dictionary = book["preparation"]["dictionary"]
            .as_object()
            .map(|d| {
                d.iter()
                    .map(|(k, v)| format!("{k}={}", v.as_str().unwrap_or("")))
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .unwrap_or_default();
        self.chapter = self
            .chapter
            .min(list(&book["chapters"]).len().saturating_sub(1));
        self.book = book;
        self.dirty.clear();
    }
    fn events(&mut self) {
        while let Ok(event) = self.rx.try_recv() {
            match event {
                Event::Ready(result) => match result {
                    Ok((api, engine)) => {
                        self.api = Some(api);
                        self.engine = Some(engine);
                    }
                    Err(e) => {
                        self.launch_failed = true;
                        self.notice = Some((e, true));
                    }
                },
                Event::Snapshot(result) => {
                    self.polling = false;
                    match result {
                        Ok(value) => {
                            if self.model_dir.is_empty() {
                                self.model_dir = s(&value["settings"], "model_directory").into();
                                self.espeech_dir = s(&value["settings"], "espeech_directory").into();
                                self.device = s(&value["settings"], "device").into();
                                self.backend = s(&value["settings"], "backend").into();
                            }
                            if self.voice.is_empty() {
                                let voices = list(&value["voices"]);
                                let chosen = self
                                    .prefs
                                    .voices
                                    .get(&self.prefs.book)
                                    .cloned()
                                    .unwrap_or(self.prefs.default_voice.clone());
                                self.voice = voices
                                    .iter()
                                    .find(|v| s(v, "id") == chosen)
                                    .or_else(|| voices.iter().find(|v| s(v, "name") == "Мой голос"))
                                    .or(voices.first())
                                    .map(|v| s(v, "id").to_owned())
                                    .unwrap_or_default();
                            }
                            if self.preview_wait && self.busy.is_empty() {
                                if let Some(job) = list(&value["jobs"])
                                    .iter()
                                    .find(|j| Some(s(j, "id")) == self.preview_job.as_deref())
                                    .cloned()
                                {
                                    if s(&job, "status") == "done" {
                                        self.preview_wait = false;
                                        self.play_job(&job);
                                    } else if ["failed", "paused"].contains(&s(&job, "status")) {
                                        self.preview_wait = false;
                                        self.notice = Some((
                                            format!(
                                                "Проба: {}. {}",
                                                status(&job),
                                                s(&job, "error")
                                            ),
                                            true,
                                        ));
                                    }
                                }
                            }
                            self.snapshot = value;
                        }
                        Err(e) => self.notice = Some((e, true)),
                    }
                }
                Event::Action(done, result) => {
                    self.busy.clear();
                    self.last_poll = Instant::now() - Duration::from_secs(5);
                    match result {
                        Err(e) => self.notice = Some((e, true)),
                        Ok(v) => {
                            match done {
                                Done::Book(id) => {
                                    self.prefs.book = id;
                                    self.chapter = 0;
                                    self.edit = false;
                                    self.read_book(v);
                                    self.voice = self
                                        .prefs
                                        .voices
                                        .get(&self.prefs.book)
                                        .cloned()
                                        .unwrap_or(self.prefs.default_voice.clone());
                                    self.page = Page::Studio;
                                    self.persist();
                                }
                                Done::UpdatedBook | Done::EditedBook => {
                                    self.read_book(v);
                                    self.notice = Some(("Изменения сохранены".into(), false));
                                }
                                Done::Imported => {
                                    self.prefs.book = s(&v, "id").into();
                                    self.chapter = 0;
                                    self.read_book(v["book"].clone());
                                    self.page = Page::Studio;
                                    self.persist();
                                }
                                Done::Render(preview) => {
                                    if preview {
                                        self.preview_job = Some(s(&v, "id").into());
                                        self.preview_wait = true;
                                    }
                                    self.notice = Some((
                                        (if preview {
                                            "Готовлю пробу. Она прозвучит после синтеза"
                                        } else {
                                            "Озвучка добавлена в очередь"
                                        })
                                        .into(),
                                        false,
                                    ));
                                }
                                Done::Voice => {
                                    self.voice = s(&v, "id").into();
                                    self.prefs.default_voice = self.voice.clone();
                                    self.prefs
                                        .voices
                                        .insert(self.prefs.book.clone(), self.voice.clone());
                                    self.persist();
                                    self.recorded = None;
                                    self.notice=Some(("Голос сохранён и выбран для озвучки. Откройте книгу в студии".into(),false));
                                }
                                Done::Prepared => self.prepared = Some(s(&v, "text").into()),
                                Done::Repair | Done::AppliedTake => {
                                    if matches!(done, Done::AppliedTake) {
                                        self.player = None;
                                        self.notice = Some(("Фрагмент заменён. Сохраните новый MP3, чтобы услышать правку в файле".into(), false));
                                    }
                                    if self.repair.is_none() || self.repair.as_ref().map(|r| s(r,"id")) != Some(s(&v,"id")) {
                                        self.repair_position = 0;
                                        self.repair_text = s(&v["chunks"][0],"text").into();
                                    }
                                    self.repair = Some(v);
                                }
                                Done::Rules => {
                                    self.rules = Some(v.as_object().map(|o| o.iter().map(|(k,v)| format!("{k}={}",v.as_str().unwrap_or(""))).collect::<Vec<_>>().join("\n")).unwrap_or_default());
                                }
                                Done::RulesSaved => self.notice = Some(("Правила сохранены для следующих озвучек. Готовый звук не изменился".into(),false)),
                                Done::RepairText => self.repair_text = s(&v,"text").into(),
                                Done::Play(label) => {
                                    let paths = list(&v["files"])
                                        .iter()
                                        .filter_map(|p| p.as_str().map(PathBuf::from))
                                        .collect::<Vec<_>>();
                                    if paths.is_empty() {
                                        self.notice =
                                            Some(("Готового звука пока нет".into(), true));
                                    } else {
                                        match Player::files(paths, label) {
                                            Ok(p) => {
                                                self.player = Some(p);
                                                self.notice = None;
                                            }
                                            Err(e) => self.notice = Some((e, true)),
                                        }
                                    }
                                }
                                Done::Saved(path) => {
                                    self.prefs.export_dir = PathBuf::from(&path)
                                        .parent()
                                        .unwrap_or(std::path::Path::new(""))
                                        .to_string_lossy()
                                        .into();
                                    self.exported.insert(0, path.clone());
                                    self.persist();
                                    self.notice = Some((format!("Сохранено: {path}"), false));
                                }
                                Done::Deleted => {
                                    self.book = Value::Null;
                                    self.prefs.book.clear();
                                    self.page = Page::Home;
                                    self.persist();
                                }
                                Done::Catalog => self.catalog = Some(v),
                                Done::Download => self.download = v,
                                Done::Hardware => self.hardware = v,
                                Done::Changed => {
                                    self.notice = Some(("Готово".into(), false));
                                }
                            }
                        }
                    }
                }
            }
        }
        if self.api.is_some() && !self.polling && self.last_poll.elapsed() > Duration::from_secs(2)
        {
            self.polling = true;
            self.last_poll = Instant::now();
            let api = self.api.clone().unwrap();
            let tx = self.tx.clone();
            std::thread::spawn(move || {
                let _ = tx.send(Event::Snapshot(api.snapshot()));
            });
        }
    }
    fn import_book(&mut self) {
        if let Some(path) = rfd::FileDialog::new()
            .set_title("Добавить книгу")
            .add_filter("Книги", &["epub", "fb2", "pdf"])
            .pick_file()
        {
            self.task(
                "Импортирую книгу…",
                Done::Imported,
                move |api| {
                    let bytes = std::fs::read(&path).map_err(|e| e.to_string())?;
                    api.upload(
                        "/import",
                        path.file_name().unwrap().to_string_lossy().into(),
                        bytes,
                        vec![],
                    )
                },
            );
        }
    }
    fn voice_name(&self) -> String {
        list(&self.snapshot["voices"])
            .iter()
            .find(|v| s(v, "id") == self.voice)
            .map(|v| s(v, "name").into())
            .unwrap_or("Выберите голос".into())
    }
    fn voice_select(&mut self, ui: &mut egui::Ui) {
        let old = self.voice.clone();
        egui::ComboBox::from_id_salt("voice-select")
            .width(ui.available_width() - 12.0)
            .selected_text(self.voice_name())
            .show_ui(ui, |ui| {
                let voices = list(&self.snapshot["voices"]);
                for (language, label) in VOICE_GROUPS {
                    let group = voices.iter().filter(|v| voice_group(v) == language);
                    if group.clone().next().is_some() { ui.strong(label); }
                    for v in group { ui.selectable_value(&mut self.voice, s(v, "id").into(), s(v, "name")); }
                }
            });
        if old != self.voice {
            self.prefs.default_voice = self.voice.clone();
            self.prefs
                .voices
                .insert(self.prefs.book.clone(), self.voice.clone());
            self.persist();
        }
    }
    fn options(&self) -> Result<Value, String> {
        let mut dictionary = BTreeMap::new();
        for line in self.dictionary.lines().filter(|l| !l.trim().is_empty()) {
            let Some((a, b)) = line.split_once('=') else {
                return Err("В словаре используйте формат: слово=произношение".into());
            };
            if a.trim().is_empty() {
                return Err("В словаре есть пустое слово".into());
            }
            dictionary.insert(a.trim(), b.trim());
        }
        Ok(
            json!({"numbers":self.numbers,"auto_stress":self.auto_stress,"remove_notes":self.remove_notes,"remove_page_furniture":self.remove_furniture,"dictionary":dictionary}),
        )
    }
    fn render(&mut self, preview: bool, chapter_only: bool) {
        match self.options() {
            Err(e) => self.notice = Some((e, true)),
            Ok(mut options) => {
                options["voice_id"] = json!(self.voice);
                options["denoise"] = json!(self.prefs.denoise);
                options["preview"] = json!(preview);
                options["chapter"] = if preview || chapter_only {
                    json!(self.chapter)
                } else {
                    Value::Null
                };
                self.prefs
                    .voices
                    .insert(self.prefs.book.clone(), self.voice.clone());
                self.prefs.default_voice = self.voice.clone();
                self.persist();
                self.post(
                    format!("/projects/{}/render", self.prefs.book),
                    options,
                    "Запускаю озвучку…",
                    Done::Render(preview),
                );
            }
        }
    }
    fn prepare(&mut self) {
        match self.options() {
            Err(e) => self.notice = Some((e, true)),
            Ok(mut v) => {
                v["chapter"] = json!(self.chapter);
                self.post(
                    format!("/projects/{}/prepare", self.prefs.book),
                    v,
                    "Подготавливаю текст…",
                    Done::Prepared,
                );
            }
        }
    }
    fn play_job(&mut self, job: &Value) {
        let route = format!("/jobs/{}/playback", s(job, "id"));
        let label = format!(
            "{} · {}",
            s(job, "title"),
            if job["preview"] == true {
                "Проба"
            } else {
                "Озвучка"
            }
        );
        self.task(
            "Открываю аудио…",
            Done::Play(label),
            move |api| api.get(&route),
        );
    }
    fn export_mp3(&mut self, job: &Value) {
        let label = if s(job, "scope") == "chapter" {
            format!(
                "{} — {}",
                s(job, "title"),
                job["chapter_titles"][0].as_str().unwrap_or("глава")
            )
        } else {
            s(job, "title").to_owned()
        };
        let name = label
            .chars()
            .map(|c| if "<>:\"/\\|?*".contains(c) { '_' } else { c })
            .collect::<String>();
        let mut dialog = rfd::FileDialog::new()
            .set_title("Сохранить всю озвучку одним MP3")
            .add_filter("Аудиокнига MP3", &["mp3"])
            .set_file_name(format!("{name} — {}.mp3", self.job_voice(job)));
        if !self.prefs.export_dir.is_empty() {
            dialog = dialog.set_directory(&self.prefs.export_dir);
        }
        if let Some(path) = dialog.save_file() {
            let path = path.with_extension("mp3").to_string_lossy().to_string();
            self.post(
                format!("/jobs/{}/export-mp3", s(job, "id")),
                json!({"path":path}),
                "Сохраняю MP3…",
                Done::Saved(path),
            );
        }
    }
    fn job_voice(&self, job: &Value) -> String {
        list(&self.snapshot["voices"])
            .iter()
            .find(|v| s(v, "id") == s(job, "voice_id"))
            .map(|v| s(v, "name").into())
            .unwrap_or("Голос из предыдущей версии".into())
    }
    fn book_jobs(&self) -> Vec<Value> {
        list(&self.snapshot["jobs"])
            .into_iter()
            .filter(|j| {
                s(j, "project_id") == self.prefs.book
                    && !j["preview"].as_bool().unwrap_or(false)
                    && s(j, "scope") != "chapter"
            })
            .collect()
    }
    fn pronunciation_windows(&mut self, ctx: &egui::Context) {
        if let Some(data) = self.repair.clone() {
            let mut open = true;
            egui::Window::new("Исправить произношение").open(&mut open).default_width(760.0).max_height((ctx.available_rect().height()-60.0).max(320.0)).vscroll(true).show(ctx, |ui| {
                ui.spacing_mut().item_spacing.y = 12.0;
                ui.label("Выберите фрагмент, сравните дубли и оставьте лучший.");
                hint(ui,self.palette,"Используется голос и модель этой записи. Остальная книга останется без изменений.");
                if let Some(job)=list(&self.snapshot["jobs"]).iter().find(|j| s(j,"id")==s(&data,"id")) {
                    ui.label(format!("Голос записи: {}",self.job_voice(job)));
                }
                let chunks=list(&data["chunks"]);
                ui.add(egui::TextEdit::singleline(&mut self.repair_search).hint_text("Найти слово или фразу в записи").desired_width(f32::INFINITY));
                let query=self.repair_search.to_lowercase();
                let before=self.repair_position;
                egui::ComboBox::from_id_salt("repair_chunk").width(690.0).selected_text(format!("Фрагмент {} из {}",before+1,chunks.len())).show_ui(ui,|ui| {
                    for (i,c) in chunks.iter().enumerate() {
                        if !query.is_empty() && !s(c,"text").to_lowercase().contains(&query) {continue;}
                        ui.selectable_value(&mut self.repair_position,i,format!("{} · {}",i+1,s(c,"text").chars().take(72).collect::<String>()));
                    }
                });
                if !query.is_empty() {hint(ui,self.palette,format!("Найдено фрагментов: {}. Выберите нужный в списке.",chunks.iter().filter(|c|s(c,"text").to_lowercase().contains(&query)).count()));}
                if before!=self.repair_position {self.repair_text=s(&chunks[self.repair_position],"text").into();}
                let position=self.repair_position;
                if let Some(chunk)=chunks.get(position) {
                    ui.add_enabled_ui(self.busy.is_empty(),|ui| {
                        if ui.button("Слушать текущий фрагмент").clicked() {
                            let route=format!("/jobs/{}/playback",s(&data,"id"));
                            self.task("Открываю звук…",Done::Play(format!("Фрагмент {}",position+1)),move |api| {
                                let v=api.get(&route)?;Ok(json!({"files":[v["files"][position]]}))
                            });
                        }
                        ui.label("Текст для новых дублей");
                        ui.add(egui::TextEdit::multiline(&mut self.repair_text).desired_rows(4).desired_width(f32::INFINITY));
                        if ui.button("Убрать знаки ударения из текста дублей").clicked() {
                            let mut chars=self.repair_text.chars().peekable();let mut plain=String::new();
                            while let Some(ch)=chars.next() {
                                if ch=='\u{301}' || ch=='\u{2ca}' {continue;}
                                if ch=='+' && chars.peek().is_some_and(|c| "аеёиоуыэюяАЕЁИОУЫЭЮЯ".contains(*c)) {continue;}
                                plain.push(ch);
                            }
                            self.repair_text=plain;
                        }
                        hint(ui,self.palette,"Сначала попробуйте тот же текст. Правки здесь влияют только на этот фрагмент озвучки, исходная книга сохранится.");
                        ui.horizontal_wrapped(|ui| {
                            if ui.button("Создать 3 дубля").clicked() {
                                self.post(format!("/jobs/{}/takes",s(&data,"id")),json!({"position":position,"text":self.repair_text,"count":3}),"Добавляю дубли…",Done::Repair);
                            }
                            if ui.button("Обновить дубли").clicked() {
                                let route=format!("/jobs/{}/repair",s(&data,"id"));
                                self.task("Обновляю…",Done::Repair,move |api| api.get(&route));
                            }
                            if ui.button("Словарь фраз…").clicked() {self.task("Открываю словарь…",Done::Rules,|api| api.get("/pronunciation"));}
                            if ui.button("Подготовить произношение").clicked() {
                                self.post("/pronunciation/preview".into(),json!({"text":self.repair_text}),"Применяю правила…",Done::RepairText);
                            }
                        });
                    });
                    ui.separator();
                    egui::ScrollArea::vertical().id_salt("takes_scroll").max_height(280.0).show(ui,|ui| {
                        for (n,take) in list(&data["takes"]).iter().filter(|t| t["position"].as_u64()==Some(position as u64)).enumerate() {
                            ui.push_id(s(take,"id"),|ui| {
                                let live=list(&self.snapshot["jobs"]).into_iter().find(|j| s(j,"id")==s(take,"id")).unwrap_or(take.clone());
                                let label=if take["original"]==true {"Исходный звук".to_owned()} else {format!("Дубль {n}")};
                                ui.horizontal_wrapped(|ui| {
                                    ui.label(format!("{label} · {}",status(&live))).on_hover_text(s(take,"text"));
                                    let selected=take["cache_key"]==chunk["cache_key"];
                                    if selected {ui.label("Выбран");}
                                    ui.add_enabled_ui(self.busy.is_empty() && s(&live,"status")=="done",|ui| {
                                        if ui.button("Слушать").clicked() {
                                            let route=format!("/jobs/{}/playback",s(take,"id"));
                                            self.task("Открываю дубль…",Done::Play(label.clone()),move |api| api.get(&route));
                                        }
                                        if !selected && ui.button("Заменить фрагмент").clicked() {
                                            self.post(format!("/jobs/{}/choose-take",s(&data,"id")),json!({"position":position,"take":take["id"],"expected_key":chunk["cache_key"]}),"Заменяю фрагмент…",Done::AppliedTake);
                                        }
                                    });
                                    if ["failed","paused"].contains(&s(&live,"status")) && ui.button("Повторить").clicked() {
                                        self.post(format!("/jobs/{}/resume",s(take,"id")),json!({}),"Возобновляю дубль…",Done::Changed);
                                    }
                                    if ["queued","running"].contains(&s(&live,"status")) && ui.button("Пауза").clicked() {
                                        self.post(format!("/jobs/{}/pause",s(take,"id")),json!({}),"Приостанавливаю дубль…",Done::Changed);
                                    }
                                });
                                if !s(&live,"error").is_empty(){ui.label(s(&live,"error"));}
                                if s(take,"text")!=self.repair_text {
                                    ui.collapsing("Текст этого дубля",|ui| {hint(ui,self.palette,s(take,"text"));});
                                }
                                ui.separator();
                            });
                        }
                    });
                    hint(ui,self.palette,"Исходный звук можно вернуть из списка. Уже сохранённые MP3 не меняются: после выбора экспортируйте книгу снова.");
                }
            });
            if !open {
                self.repair = None;
            }
        }
        if let Some(mut rules) = self.rules.clone() {
            let mut open = true;
            egui::Window::new("Словарь произношения").open(&mut open).default_width(640.0).max_height((ctx.available_rect().height()-60.0).max(320.0)).vscroll(true).show(ctx,|ui| {
                ui.spacing_mut().item_spacing.y=12.0;
                ui.label("Одна строка: исходная фраза=текст для модели");
                hint(ui,self.palette,"Совпадение точное, с учётом регистра. Правила применяются при следующей озвучке; правила книги имеют приоритет. Готовые записи не меняются.");
                ui.add(egui::TextEdit::multiline(&mut rules).desired_rows(9).desired_width(f32::INFINITY).hint_text("к мосту=к мосту́"));
                hint(ui,self.palette,"Для ESpeech укажите ударение: мост+у или мосту́. Ручная пометка заменит автоматическую. Сначала послушайте дубль, затем замените фрагмент. Старые задачи Qwen сохраняют свой движок.");
                ui.horizontal_wrapped(|ui| {
                    if ui.button("Добавить пример «к мосту»").clicked() {if !rules.is_empty(){rules.push('\n');}rules.push_str("к мосту=к мосту́");}
                    if ui.button("Добавить фразы с X/Х").clicked() {if !rules.is_empty(){rules.push('\n');}rules.push_str("что X=что икс\nчто Х=что икс\nпротивоположна X=противоположна икс\nпротивоположна Х=противоположна икс");}
                    if ui.button("Загрузить TXT…").clicked() {
                        if let Some(path)=rfd::FileDialog::new().add_filter("Словарь UTF-8", &["txt"]).pick_file() {
                            match std::fs::read_to_string(path) {Ok(text)=>rules=text,Err(e)=>self.notice=Some((format!("Не удалось загрузить словарь: {e}"),true))}
                        }
                    }
                    if ui.button("Сохранить TXT…").clicked() {
                        if let Some(path)=rfd::FileDialog::new().add_filter("Словарь UTF-8", &["txt"]).set_file_name("Произношение.txt").save_file() {
                            if let Err(e)=std::fs::write(path,&rules) {self.notice=Some((format!("Не удалось сохранить словарь: {e}"),true));}
                        }
                    }
                    if ui.add_enabled(self.busy.is_empty(),egui::Button::new("Сохранить правила")).clicked() {
                        let mut map=BTreeMap::new();let mut valid=true;
                        for line in rules.lines().filter(|l| !l.trim().is_empty()) {
                            if let Some((a,b))=line.split_once('=') {if a.trim().is_empty()||b.trim().is_empty(){valid=false;}map.insert(a.trim(),b.trim());} else {valid=false;}
                        }
                        if valid {self.post("/pronunciation".into(),json!({"rules":map}),"Сохраняю словарь…",Done::RulesSaved);} else {self.notice=Some(("Заполните обе части каждой строки: фраза=произношение".into(),true));}
                    }
                });
            });
            self.rules = if open { Some(rules) } else { None };
        }
    }
    fn job_controls(&mut self, ui: &mut egui::Ui, job: &Value, full: bool) {
        let total = job["total"].as_f64().unwrap_or(1.0).max(1.0);
        let completed = job["completed"].as_f64().unwrap_or(0.0);
        ui.label(RichText::new(status(job)).strong());
        if s(job, "status") != "done" {
            ui.add(
                egui::ProgressBar::new((completed / total) as f32)
                    .text(format!(
                        "{} / {} фрагментов",
                        completed as u64, total as u64
                    ))
                    .fill(self.palette.accent),
            );
        }
        hint(
            ui,
            self.palette,
            format!(
                "{} · {} звука",
                self.job_voice(job),
                duration(job["audio_seconds"].as_f64().unwrap_or(0.0))
            ),
        );
        if !s(job, "error").is_empty() {
            ui.colored_label(
                if self.prefs.dark {
                    c(0xffb5ad)
                } else {
                    c(0x9d3028)
                },
                s(job, "error"),
            );
        }
        ui.horizontal_wrapped(|ui| {
            if ["running", "queued"].contains(&s(job, "status"))
                && ui.button("Приостановить").clicked()
            {
                self.post(
                    format!("/jobs/{}/pause", s(job, "id")),
                    json!({}),
                    "Приостанавливаю…",
                    Done::Changed,
                );
            }
            if ["paused", "failed"].contains(&s(job, "status")) && ui.button("Продолжить").clicked()
            {
                self.post(
                    format!("/jobs/{}/resume", s(job, "id")),
                    json!({}),
                    "Продолжаю…",
                    Done::Changed,
                );
            }
            if completed > 0.0 && ui.button("Слушать").clicked() {
                self.play_job(job);
            }
            if s(job, "status") == "done" && ui.button("Сохранить MP3…").clicked() {
                self.export_mp3(job);
            }
        });
        if full && s(job, "status") == "done" {
            ui.collapsing("Экспорт M4B и MP3 по главам", |ui| {
                if ui.button("MP3 по главам + M4B…").clicked() {
                    if let Some(path) = rfd::FileDialog::new()
                        .set_title("Новая папка для экспорта")
                        .pick_folder()
                    {
                        let route = format!("/jobs/{}/export", s(job, "id"));
                        self.post(
                            route,
                            json!({"directory":path.to_string_lossy()}),
                            "Экспортирую главы и M4B…",
                            Done::Saved(path.to_string_lossy().into()),
                        );
                    }
                }
            });
        }
        if s(job, "status") == "done"
            && job["repair_parent"].is_null()
            && ui.button("Исправить произношение").clicked()
        {
            let route = format!("/jobs/{}/repair", s(job, "id"));
            self.task(
                "Открываю фрагменты…",
                Done::Repair,
                move |api| api.get(&route),
            );
        }
        if s(job, "status") == "running" && completed == 0.0 {
            hint(
                ui,
                self.palette,
                "Загрузка модели и первый фрагмент могут занять около минуты.",
            );
        }
    }
    fn navigate(&mut self, page: Page) {
        if self.recording.is_some() {
            self.finish_recording();
        }
        self.page = page;
    }
    fn top(&mut self, ctx: &egui::Context) {
        let p = self.palette;
        egui::SidePanel::left("navigation")
            .exact_width(208.0).resizable(false)
            .frame(egui::Frame::new().fill(if self.prefs.dark { c(0x242427) } else { c(0xeeeef2) }).inner_margin(16))
            .show(ctx, |ui| {
                ui.add_space(12.0);
                ui.horizontal(|ui| {
                    ui.spacing_mut().item_spacing.x = 8.0;
                    ui.add(egui::Image::new((self.brand_icon.id(), egui::vec2(52.0, 52.0))).corner_radius(9.0));
                    ui.label(RichText::new("Lunatica").font(serif(30.0)));
                });
                ui.add_space(8.0);
                hint(ui,p,"Студия аудиокниг");
                ui.add_space(32.0);
                for (page,label) in [(Page::Home,"Библиотека"),(Page::Studio,"Студия"),(Page::Voices,"Мои голоса"),(Page::Exports,"Готовые записи")] {
                    let selected=self.page==page;
                    let button=egui::Button::new(RichText::new(label).size(15.0).color(if selected && !self.prefs.dark {c(0x65438b)}else{p.text}))
                        .fill(if selected {p.soft}else{Color32::TRANSPARENT})
                        .stroke(Stroke::NONE);
                    if ui.add_sized([ui.available_width(),44.0],button).clicked() {
                        self.navigate(page);
                        if page==Page::Studio && self.book.is_null() && !self.prefs.book.is_empty() {self.open_book(self.prefs.book.clone());}
                    }
                    ui.add_space(4.0);
                }
                ui.add_space(20.0);
                if ui.add_enabled(self.api.is_some() && self.busy.is_empty(),egui::Button::new(RichText::new("Добавить книгу…").color(p.ink)).fill(p.accent).min_size(egui::vec2(ui.available_width(),44.0))).clicked(){self.import_book();}
                ui.with_layout(egui::Layout::bottom_up(egui::Align::Min),|ui|{
                    ui.add_space(8.0);
                    hint(ui,p,"Книги и голос — локально");
                    ui.add_space(12.0);
                    if ui.add_sized([ui.available_width(),40.0],egui::Button::new(if self.prefs.dark{"Светлая тема"}else{"Тёмная тема"})).clicked(){
                        self.prefs.dark=!self.prefs.dark;self.palette=Palette::new(self.prefs.dark);Self::theme(ctx,self.palette,self.prefs.dark);self.persist();
                    }
                    if ui.add_sized([ui.available_width(),40.0],egui::Button::new("Настройки").selected(self.page==Page::Settings)).clicked(){self.navigate(Page::Settings);}
                });
            });
    }
    fn cover(
        &mut self,
        ui: &mut egui::Ui,
        book: &Value,
        width: f32,
        height: f32,
        index: usize,
    ) -> egui::Response {
        let (rect, response) =
            ui.allocate_exact_size(Vec2::new(width, height), egui::Sense::click());
        response.widget_info(|| {
            egui::WidgetInfo::labeled(
                egui::WidgetType::Button,
                ui.is_enabled(),
                format!("Открыть {}", s(book, "title")),
            )
        });
        let key = s(book, "cover_id").to_owned();
        if !key.is_empty() && key.chars().all(|c| c.is_ascii_hexdigit() || c == '-') {
            if !self.covers.contains_key(&key) && !self.failed_covers.contains(&key) {
                match image::open(
                    backend::data_dir()
                        .join("covers")
                        .join(format!("{key}.jpg")),
                ) {
                    Ok(img) => {
                        let img = img.to_rgba8();
                        let image = egui::ColorImage::from_rgba_unmultiplied(
                            [img.width() as usize, img.height() as usize],
                            &img,
                        );
                        self.covers.insert(
                            key.clone(),
                            ui.ctx()
                                .load_texture(&key, image, egui::TextureOptions::LINEAR),
                        );
                    }
                    Err(_) => {
                        self.failed_covers.insert(key.clone());
                    }
                }
            }
            if let Some(texture) = self.covers.get(&key) {
                ui.painter().image(
                    texture.id(),
                    rect,
                    egui::Rect::from_min_max(egui::Pos2::ZERO, egui::pos2(1.0, 1.0)),
                    Color32::WHITE,
                );
                return response.on_hover_text("Открыть в студии");
            }
        }
        let colors = [
            (0x423543, 0xe6d4ea),
            (0x354344, 0xd1e3df),
            (0x504539, 0xeee1cc),
            (0x3d3b51, 0xdedbf3),
        ];
        let (bg, fg) = colors[index % colors.len()];
        let painter = ui.painter();
        painter.rect_filled(rect, 4, c(bg));
        painter.rect_stroke(
            rect.shrink(12.0),
            0,
            Stroke::new(1.0_f32, c(fg).gamma_multiply(0.4)),
            egui::StrokeKind::Inside,
        );
        painter.line_segment(
            [
                rect.left_top() + Vec2::new(6.0, 0.0),
                rect.left_bottom() + Vec2::new(6.0, 0.0),
            ],
            Stroke::new(2.0_f32, c(0x1d1820)),
        );
        if width < 110.0 {
            let initial=s(book,"title").chars().find(|ch|ch.is_alphanumeric()).unwrap_or('L').to_string();
            painter.text(rect.center(),egui::Align2::CENTER_CENTER,initial,serif((width*0.40).min(30.0)),c(fg));
        } else {
            let galley=painter.layout(s(book,"title").to_owned(),serif(if width>150.0 {23.0}else{16.0}),c(fg),width-40.0);
            painter.with_clip_rect(rect.shrink(12.0)).galley(rect.left_top()+Vec2::new(24.0,32.0),galley,c(fg));
            painter.text(rect.left_bottom()+Vec2::new(24.0,-30.0),egui::Align2::LEFT_BOTTOM,"LUNATICA",FontId::proportional(9.0),c(fg));
        }
        if response.hovered() {
            painter.rect_stroke(
                rect,
                4,
                Stroke::new(2.0_f32, self.palette.accent),
                egui::StrokeKind::Inside,
            );
        }
        response.on_hover_text("Открыть в студии")
    }
    fn home(&mut self, ui: &mut egui::Ui) {
        let p=self.palette;
        let projects=list(&self.snapshot["projects"]);
        title(ui,"Библиотека",30.0);
        hint(ui,p,"Выберите книгу, чтобы продолжить озвучку.");
        ui.add_space(20.0);
        if projects.is_empty(){
            panel(p).show(ui,|ui|{
                ui.set_width(ui.available_width());
                title(ui,"Ваша первая аудиокнига",24.0);
                ui.label("Добавьте EPUB, FB2 или PDF. Затем выберите свой голос и послушайте пробу.");
                ui.add_space(12.0);
                if primary(ui,p,"Добавить книгу…").clicked(){self.import_book();}
            });
            return;
        }
        let current=projects.iter().find(|b|s(b,"id")==self.prefs.book).unwrap_or(&projects[0]).clone();
        panel(p).stroke(Stroke::NONE).show(ui,|ui|{
            ui.set_width(ui.available_width());
            ui.horizontal_top(|ui|{
                if self.cover(ui,&current,76.0,108.0,0).clicked(){self.open_book(s(&current,"id").into());}
                ui.add_space(8.0);
                let info_width=(ui.available_width()-212.0).max(180.0);
                ui.allocate_ui_with_layout(Vec2::new(info_width,108.0),egui::Layout::top_down(egui::Align::Min),|ui|{
                    ui.set_width(info_width);
                    hint(ui,p,"ПРОДОЛЖИТЬ РАБОТУ");
                    ui.add(egui::Label::new(RichText::new(s(&current,"title")).font(serif(24.0))).wrap());
                    hint(ui,p,s(&current,"author"));
                    ui.label(RichText::new(status(&current["render"])).size(14.0));
                });
                ui.vertical(|ui|{
                    ui.set_width(188.0);
                    if primary(ui,p,"Открыть в студии").clicked(){self.open_book(s(&current,"id").into());}
                    hint(ui,p,"Текст, голос и озвучка");
                });
            });
        });
        ui.add_space(24.0);
        ui.horizontal(|ui|{
            ui.label(RichText::new(format!("Все книги · {}",projects.len())).strong().size(18.0));
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center),|ui|{
                ui.add(egui::TextEdit::singleline(&mut self.search).hint_text("Название или автор").margin(egui::Margin::symmetric(12,10)).desired_width(260.0));
            });
        });
        ui.add_space(12.0);
        let query=self.search.to_lowercase();
        let filtered:Vec<_>=projects.into_iter().filter(|b|format!("{} {}",s(b,"title"),s(b,"author")).to_lowercase().contains(&query)).collect();
        if filtered.is_empty(){ui.label("Ничего не найдено. Попробуйте другое название или автора.");}
        for (index,book) in filtered.iter().enumerate(){
            ui.push_id(s(book,"id"),|ui|{
                egui::Frame::new().fill(p.surface).corner_radius(8).inner_margin(16).show(ui,|ui|{
                    ui.set_width(ui.available_width());
                    ui.horizontal(|ui|{
                        if self.cover(ui,book,48.0,68.0,index).clicked(){self.open_book(s(book,"id").into());}
                        ui.add_space(4.0);
                        let text_width=(ui.available_width()-262.0).max(140.0);
                        ui.allocate_ui_with_layout(Vec2::new(text_width,68.0),egui::Layout::top_down(egui::Align::Min),|ui|{
                            ui.set_width(text_width);
                            ui.add(egui::Label::new(RichText::new(s(book,"title")).size(17.0).strong()).truncate());
                            ui.add(egui::Label::new(RichText::new(s(book,"author")).color(p.muted).size(13.0)).truncate());
                        });
                        ui.allocate_ui_with_layout(Vec2::new(130.0,40.0),egui::Layout::left_to_right(egui::Align::Center),|ui|{ui.set_width(130.0);hint(ui,p,status(&book["render"]));});
                        if ui.add_sized([96.0,40.0],egui::Button::new("Открыть")).clicked(){self.open_book(s(book,"id").into());}
                    });
                });
            });
            ui.add_space(4.0);
        }
    }
    fn studio(&mut self, ui: &mut egui::Ui) {
        let p = self.palette;
        if self.book.is_null() {
            title(ui, "Выберите книгу", 32.0);
            ui.label("Откройте книгу с вашей полки или добавьте новую.");
            if ui.button("К моим книгам").clicked() {
                self.page = Page::Home;
            }
            return;
        }
        ui.horizontal(|ui| {
            if ui.small_button("← Мои книги").clicked() {
                self.navigate(Page::Home);
            }
            hint(ui, p, "СТУДИЯ");
        });
        title(ui, s(&self.book, "title"), 30.0);
        if !s(&self.book, "author").is_empty() {
            hint(ui, p, s(&self.book, "author"));
        }
        for warning in list(&self.book["warnings"]) {
            hint(ui, p, warning.as_str().unwrap_or(""));
        }
        ui.add_space(14.0);
        let width = ui.available_width();
        let side = (width * 0.36).clamp(296.0, 352.0);
        ui.horizontal_top(|ui| {
            ui.allocate_ui_with_layout(
                Vec2::new(width - side - 16.0, ui.available_height()),
                egui::Layout::top_down(egui::Align::Min),
                |ui| {
                    ui.set_width(width-side-16.0);
                    self.book_page(ui);
                },
            );
            ui.add_space(4.0);
            ui.allocate_ui_with_layout(
                Vec2::new(side, ui.available_height()),
                egui::Layout::top_down(egui::Align::Min),
                |ui| {
                    ui.set_width(side);
                    panel(p).inner_margin(20).show(ui, |ui| {
                        ui.spacing_mut().item_spacing.y = 10.0;
                        ui.spacing_mut().interact_size.y = 36.0;
                        ui.label(RichText::new("1. Голос").strong().size(18.0));
                        ui.add_space(4.0);
                        let chapters = list(&self.book["chapters"]);
                        if self.chapter < chapters.len() {
                            ui.vertical(|ui|{ui.label("Язык озвучки главы");let mut lang=s(&chapters[self.chapter],"language").to_owned();let old=lang.clone();egui::ComboBox::from_id_salt("speech-language").selected_text(&lang).show_ui(ui,|ui|{for(key,name)in[("ru","Русский"),("en","English"),("de","Deutsch"),("fr","Français"),("es","Español"),("it","Italiano"),("pt","Português"),("ja","日本語"),("ko","한국어"),("zh","中文")]{ui.selectable_value(&mut lang,key.into(),name);}});if old!=lang{self.post(format!("/projects/{}/language",self.prefs.book),json!({"chapter":self.chapter,"language":lang}),"Сохраняю язык…",Done::UpdatedBook);}});
                        }
                        ui.add_space(8.0);
                        self.voice_select(ui);
                        if secondary(ui, "Добавить голос").clicked() {
                            self.navigate(Page::Voices);
                        }
                        ui.add_space(5.0);
                        ui.separator();
                        ui.add_space(3.0);
                        ui.label(RichText::new("2. Проверка звучания").strong().size(18.0));
                        if ui
                            .checkbox(&mut self.prefs.denoise, "Снизить фоновый шум")
                            .changed()
                        {
                            self.persist();
                        }
                        ui.checkbox(&mut self.numbers, "Читать числа словами");
                        if !s(&self.snapshot["settings"], "espeech_directory").is_empty() {
                            hint(ui, p, "Русский — ESpeech с ударениями. Английский — Qwen.");
                        }

                        if ui
                            .add_enabled(
                                self.dirty.is_empty(),
                                egui::Button::new("Проверить текст для озвучки"),
                            )
                            .clicked()
                        {
                            self.prepare();
                        }
                        hint(ui, p, "Проба — до 250 символов текущей главы.");
                        let enabled =
                            !self.voice.is_empty() && self.dirty.is_empty() && !self.preview_wait;
                        ui.add_enabled_ui(enabled, |ui| {
                            if secondary(
                                ui,
                                if self.preview_wait {
                                    "Готовлю пробу…"
                                } else {
                                    "Слушать пробу"
                                },
                            )
                            .clicked()
                            {
                                self.render(true, false);
                            }
                        });
                        if let Some(job) = list(&self.snapshot["jobs"])
                            .iter()
                            .find(|j| {
                                j["preview"] == true
                                    && j["repair_parent"].is_null()
                                    && s(j, "project_id") == self.prefs.book
                                    && s(j, "voice_id") == self.voice
                                    && s(j, "status") == "done"
                            })
                            .cloned()
                        {
                            if ui.button("Слушать готовую пробу").clicked() {
                                self.play_job(&job);
                            }
                        }
                        ui.add_space(5.0);
                        ui.separator();
                        ui.add_space(3.0);
                        ui.label(RichText::new("3. Озвучка").strong().size(18.0));
                        egui::ComboBox::from_id_salt("render-scope").width(ui.available_width()-16.0).selected_text(if self.chapter_only {"Выбранная глава"}else{"Вся книга"}).show_ui(ui,|ui|{ui.selectable_value(&mut self.chapter_only,true,"Выбранная глава");ui.selectable_value(&mut self.chapter_only,false,"Вся книга");});
                        let jobs = self.book_jobs();
                        let active = jobs
                            .iter()
                            .find(|j| ["queued", "running"].contains(&s(j, "status")));
                        if let Some(job) = active {
                            self.job_controls(ui, job, false);
                        } else {
                            ui.add_enabled_ui(
                                !self.voice.is_empty() && self.dirty.is_empty(),
                                |ui| {
                                    if primary(ui, p, if self.chapter_only {"Озвучить главу"}else{"Озвучить всю книгу"}).clicked()
                                    {
                                        self.render(false, self.chapter_only);
                                    }
                                },
                            );
                            if let Some(job) = jobs.first() {
                                ui.add_space(6.0);
                                self.job_controls(ui, job, false);
                            }
                        }
                        if !self.dirty.is_empty() {
                            hint(ui, p, "Сохраните правки текста перед озвучкой.");
                        }
                        if self.voice.is_empty() {
                            hint(ui, p, "Сначала запишите или добавьте свой голос.");
                        }
                        if ui.button("Все записи и экспорт").clicked(){self.navigate(Page::Exports);}
                    });
                    ui.add_space(12.0);
                    ui.collapsing("Дополнительные параметры", |ui| {
                        ui.checkbox(&mut self.remove_notes, "Убирать сноски");
                        ui.checkbox(
                            &mut self.remove_furniture,
                            "Убирать номера страниц и колонтитулы",
                        );
                        ui.label("Подсказки произношения для этой книги");
                        ui.add(
                            egui::TextEdit::multiline(&mut self.dictionary)
                                .desired_rows(3)
                                .hint_text("смотритель=смотри́тель")
                                .desired_width(f32::INFINITY),
                        );
                        if ui.button("Проверить подготовленный текст").clicked()
                        {
                            self.prepare();
                        }
                        ui.add_enabled_ui(!self.voice.is_empty() && self.dirty.is_empty(), |ui| {
                            if ui.button("Озвучить только эту главу").clicked()
                            {
                                self.render(false, true);
                            }
                        });
                    });
                },
            );
        });
    }
    fn book_page(&mut self, ui: &mut egui::Ui) {
        let p = self.palette;
        let chapters = list(&self.book["chapters"]);
        if chapters.is_empty() {
            ui.label("В книге не найдены главы");
            return;
        }
        ui.horizontal_wrapped(|ui| {
            egui::ComboBox::from_id_salt("chapter")
                .width((ui.available_width()-24.0).min(450.0))
                .selected_text(format!(
                    "{} / {} · {}",
                    self.chapter + 1,
                    chapters.len(),
                    s(&chapters[self.chapter], "title")
                ))
                .show_ui(ui, |ui| {
                    for (i, ch) in chapters.iter().enumerate() {
                        ui.selectable_value(
                            &mut self.chapter,
                            i,
                            format!("{}. {}", i + 1, s(ch, "title")),
                        );
                    }
                });
            ui.toggle_value(&mut self.edit, "Редактировать произношение");
        });
        if !self.dirty.is_empty() {
            ui.horizontal(|ui| {
                if ui
                    .button(format!("Сохранить правки ({})", self.dirty.len()))
                    .clicked()
                {
                    let edits = self
                        .dirty
                        .iter()
                        .map(|i| (*i, self.blocks[*i].clone()))
                        .collect::<Vec<_>>();
                    let id = self.prefs.book.clone();
                    self.task(
                        "Сохраняю текст…",
                        Done::EditedBook,
                        move |api| {
                            let mut book = Value::Null;
                            for (i, text) in edits {
                                book = api.post(
                                    &format!("/projects/{id}/text"),
                                    &json!({"block":i,"text":text}),
                                )?;
                            }
                            Ok(book)
                        },
                    );
                }
                if ui.button("Отменить правки").clicked() {
                    self.read_book(self.book.clone());
                }
            });
        }
        ui.add_space(8.0);
        panel(p).show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.set_min_height(400.0);
            hint(ui, p, format!("ГЛАВА {}", self.chapter + 1));
            title(ui, s(&chapters[self.chapter], "title"), 26.0);
            ui.add_space(16.0);
            if self.edit {
                hint(
                    ui,
                    p,
                    "Поставьте курсор после гласной и нажмите «Ударение». Или введите смотр+итель.",
                );
                ui.add_space(12.0);
            }
            let start = chapters[self.chapter]["start"].as_u64().unwrap_or(0) as usize;
            let end = (chapters[self.chapter]["end"].as_u64().unwrap_or(0) as usize)
                .min(self.blocks.len());
            egui::ScrollArea::vertical()
                .id_salt("book-page")
                .max_height(480.0)
                .show(ui, |ui| {
                    for i in start..end {
                        if !self.edit
                            && i == start
                            && self.blocks[i].trim() == s(&chapters[self.chapter], "title").trim()
                        {
                            continue;
                        }
                        ui.push_id(i, |ui| {
                            if self.edit {
                                hint(ui, p, format!("Абзац {}", i + 1));

                                let output = egui::TextEdit::multiline(&mut self.blocks[i])
                                    .font(serif(18.0))
                                    .desired_width(f32::INFINITY)
                                    .show(ui);
                                if output.response.changed() {
                                    self.dirty.insert(i);
                                }
                                if ui.button("Ударение").clicked() {
                                    if let Some(range) = output.state.cursor.char_range() {
                                        let at = range.primary.index;
                                        let chars: Vec<char> = self.blocks[i].chars().collect();
                                        if at > 0
                                            && at <= chars.len()
                                            && "аеёиоуыэюяАЕЁИОУЫЭЮЯ".contains(chars[at - 1])
                                        {
                                            let byte = self.blocks[i]
                                                .char_indices()
                                                .nth(at)
                                                .map(|(i, _)| i)
                                                .unwrap_or(self.blocks[i].len());
                                            if chars.get(at) != Some(&'\u{301}') {
                                                self.blocks[i].insert(byte, '\u{301}');
                                                self.dirty.insert(i);
                                            }
                                        } else {
                                            self.notice = Some((
                                                "Поставьте курсор сразу после гласной".into(),
                                                true,
                                            ));
                                        }
                                    }
                                }
                                if self.book["spoken_overrides"][i.to_string()].is_string()
                                    && ui.small_button("Вернуть оригинал").clicked()
                                {
                                    self.post(
                                        format!("/projects/{}/text", self.prefs.book),
                                        json!({"block":i,"text":"","reset":true}),
                                        "Восстанавливаю абзац…",
                                        Done::UpdatedBook,
                                    );
                                }
                            } else {
                                ui.label(RichText::new(&self.blocks[i]).font(serif(19.0)));
                            }
                            ui.add_space(12.0);
                        });
                    }
                });
            ui.add_space(12.0);
            ui.separator();
            hint(
                ui,
                p,
                "Правки меняют только озвучиваемый текст. Исходная книга сохраняется.",
            );
        });
        ui.add_space(10.0);
        ui.collapsing("О книге и главах",|ui|{
            ui.label("Название");ui.add_sized([ui.available_width(),40.0],egui::TextEdit::singleline(&mut self.metadata_title));ui.label("Автор");ui.add_sized([ui.available_width(),40.0],egui::TextEdit::singleline(&mut self.metadata_author));
            if ui.button("Сохранить изменения").clicked(){self.post(format!("/projects/{}/metadata",self.prefs.book),json!({"title":self.metadata_title,"author":self.metadata_author}),"Сохраняю…",Done::UpdatedBook);}

            ui.collapsing("Разделить или объединить главы",|ui|{
                ui.label("Разделить перед абзацем (номер виден в режиме правки):");ui.add(egui::DragValue::new(&mut self.split_at).range(1..=self.blocks.len()));ui.text_edit_singleline(&mut self.split_title);
                ui.add_enabled_ui(self.dirty.is_empty(),|ui|{
                    if ui.button("Разделить главу").clicked(){self.post(format!("/projects/{}/split",self.prefs.book),json!({"chapter":self.chapter,"at":self.split_at.saturating_sub(1),"title":self.split_title}),"Разделяю главу…",Done::UpdatedBook);}
                    if self.chapter+1<chapters.len()&&ui.button("Объединить со следующей").clicked(){self.post(format!("/projects/{}/merge/{}",self.prefs.book,self.chapter),json!({}),"Объединяю главы…",Done::UpdatedBook);}
                });
            });
            if ui.button("Добавить обложку…").clicked(){if let Some(path)=rfd::FileDialog::new().add_filter("Обложка", &["jpg","jpeg","png"]).pick_file(){let route=format!("/projects/{}/cover",self.prefs.book);self.task("Добавляю обложку…",Done::UpdatedBook,move|api|api.upload(&route,path.file_name().unwrap().to_string_lossy().into(),std::fs::read(path).map_err(|e|e.to_string())?,vec![]));}}
            if ui.button("Удалить книгу из библиотеки…").clicked(){self.delete_confirm=true;}
        });
    }
    fn finish_recording(&mut self) {
        if let Some(recording) = self.recording.take() {
            match recording.finish() {
                Ok(wav) => {
                    self.recorded = Some(wav);
                    self.notice = Some((
                        "Запись готова. Прослушайте её и сохраните голос".into(),
                        false,
                    ));
                }
                Err(e) => self.notice = Some((e, true)),
            }
        }
    }
    fn voices(&mut self, ui: &mut egui::Ui) {
        let p = self.palette;

        title(ui, "Мои голоса", 30.0);
        ui.label("Запишите образец или выберите сохранённый голос для книги.");
        if !self.book.is_null() && ui.button("Вернуться в студию").clicked(){self.navigate(Page::Studio);}
        ui.add_space(20.0);
        let width = ui.available_width();
        ui.horizontal_top(|ui|{
            ui.allocate_ui_with_layout(Vec2::new((width-24.0)*0.55,600.0),egui::Layout::top_down(egui::Align::Min),|ui|{panel(p).show(ui,|ui|{ui.set_width(ui.available_width());
                title(ui,"Запишите свой голос",24.0);hint(ui,p,"Спокойно, обычным голосом. 10–12 секунд, без музыки и долгих пауз.");ui.add_space(12.0);
                ui.label(RichText::new(audio::SCRIPT).font(serif(20.0)));ui.add_space(18.0);
                if self.microphones.is_empty(){self.microphones=audio::microphones();}
                ui.add_enabled_ui(self.recording.is_none(),|ui|{egui::ComboBox::from_id_salt("microphone").width(ui.available_width()-12.0).selected_text(if self.microphone.is_empty(){"Системный микрофон"}else{&self.microphone}).show_ui(ui,|ui|{ui.selectable_value(&mut self.microphone,String::new(),"По умолчанию");for name in &self.microphones{ui.selectable_value(&mut self.microphone,name.clone(),name);}});});
                if let Some(rec)=&self.recording {
                    ui.label(RichText::new(format!("ИДЁТ ЗАПИСЬ · {} / 0:12",duration(rec.started.elapsed().as_secs_f64()))).color(if self.prefs.dark{c(0xffb5ad)}else{c(0x9d3028)}));
                    ui.add(egui::ProgressBar::new((rec.level()*5.0).min(1.0)).text("Уровень микрофона"));
                    if primary(ui,p,"Остановить запись").clicked(){self.finish_recording();}
                    if ui.button("Отменить запись").clicked(){self.recording=None;}
                }else {
                    if primary(ui,p,if self.recorded.is_some(){"Записать заново"}else{"Начать запись"}).clicked(){
                        self.player=None;self.recorded=None;self.transcript=audio::SCRIPT.into();
                        match Recording::start(&self.microphone){Ok(rec)=>self.recording=Some(rec),Err(e)=>self.notice=Some((e,true))}
                    }
                    hint(ui,p,"Микрофон включается только по кнопке. При уходе с экрана запись остановится.");
                }
                if self.recorded.is_some(){ui.separator();ui.label("Название голоса");ui.text_edit_singleline(&mut self.voice_name);
                    ui.collapsing("Текст записи",|ui|{ui.add(egui::TextEdit::multiline(&mut self.transcript).desired_width(f32::INFINITY).desired_rows(5));});
                    if secondary(ui,"Прослушать запись").clicked(){match Player::bytes(self.recorded.clone().unwrap()){Ok(player)=>self.player=Some(player),Err(e)=>self.notice=Some((e,true))}}
                    if primary(ui,p,"Сохранить и использовать голос").clicked(){let bytes=self.recorded.clone().unwrap();let name=self.voice_name.clone();let transcript=self.transcript.clone();self.task("Сохраняю голос…",Done::Voice,move|api|api.upload("/voices","recording.wav".into(),bytes,vec![("name",name),("transcript",transcript)]));}
                }
                ui.add_space(18.0);ui.separator();ui.collapsing("Загрузить готовый WAV",|ui|{
                    hint(ui,p,"PCM 16-bit, 16 кГц или выше, 10–12 секунд. Впишите именно тот текст, который звучит в файле.");
                    ui.label("Название");ui.text_edit_singleline(&mut self.voice_name);ui.label("Текст записи");ui.add(egui::TextEdit::multiline(&mut self.transcript).desired_rows(4).desired_width(f32::INFINITY));
                    ui.add_enabled_ui(self.recording.is_none(),|ui|{if ui.button("Выбрать WAV и добавить голос…").clicked(){if let Some(path)=rfd::FileDialog::new().add_filter("Запись голоса", &["wav"]).pick_file(){let name=self.voice_name.clone();let transcript=self.transcript.clone();self.task("Добавляю голос…",Done::Voice,move|api|api.upload("/voices",path.file_name().unwrap().to_string_lossy().into(),std::fs::read(&path).map_err(|e|e.to_string())?,vec![("name",name),("transcript",transcript)]));}}});
                });
            });});
            ui.allocate_ui_with_layout(Vec2::new((width-24.0)*0.45,500.0),egui::Layout::top_down(egui::Align::Min),|ui|{

                let mut voices=list(&self.snapshot["voices"]);voices.sort_by_key(|v|s(v,"id")!=self.voice);
                for (language, label) in VOICE_GROUPS {
                let group=voices.iter().filter(|v| voice_group(v) == language);
                if group.clone().next().is_some(){title(ui,label,24.0);}
                for voice in group{ui.push_id(s(&voice,"id"),|ui|{panel(p).show(ui,|ui|{ui.set_width(ui.available_width());
                    ui.label(RichText::new(s(&voice,"name")).size(18.0).strong());if voice["bundled"] == true {hint(ui,p,s(voice,"description"));}hint(ui,p,format!("{} секунд",voice["duration"].as_f64().unwrap_or(0.0).round()));
                    if s(&voice,"id")==self.voice{ui.label(RichText::new("Выбран").color(if self.prefs.dark{p.accent}else{c(0x65438b)}));}
                    else if secondary(ui,if self.book.is_null(){"Выбрать голос"}else{"Использовать для книги"}).clicked(){self.voice=s(&voice,"id").into();self.prefs.default_voice=self.voice.clone();self.prefs.voices.insert(self.prefs.book.clone(),self.voice.clone());self.persist();if !self.book.is_null(){self.navigate(Page::Studio);}}
                    if secondary(ui,"Слушать образец").clicked(){let route=format!("/voices/{}/playback",s(&voice,"id"));self.task("Открываю образец…",Done::Play(s(&voice,"name").into()),move|api|api.get(&route));}
                });ui.add_space(12.0);});}}
                if !self.book.is_null()&&secondary(ui,"Вернуться к книге").clicked(){self.navigate(Page::Studio);}
            });
        });
    }
    fn exports(&mut self, ui: &mut egui::Ui) {
        let p = self.palette;

        title(ui, "Готовые записи", 30.0);
        ui.label("Слушайте, исправляйте произношение и экспортируйте MP3 или M4B.");
        ui.add_space(18.0);
        let jobs = list(&self.snapshot["jobs"]);
        if jobs.is_empty() {
            ui.label("Пока нет озвученных книг. Начните с пробы в студии.");
        }
        for job in jobs.iter().filter(|j| j["preview"] != true && j["repair_parent"].is_null()) {
            ui.push_id(s(job, "id"), |ui| {
                panel(p).show(ui, |ui| {
                    ui.set_width(ui.available_width());
                    title(ui, s(job, "title"), 25.0);
                    if s(job, "scope") == "chapter" {
                        hint(
                            ui,
                            p,
                            format!(
                                "Одна глава: {}",
                                list(&job["chapter_titles"])
                                    .iter()
                                    .filter_map(Value::as_str)
                                    .collect::<Vec<_>>()
                                    .join(", ")
                            ),
                        );
                    } else {
                        hint(ui, p, "Вся книга");
                    }
                    self.job_controls(ui, job, true);
                });
                ui.add_space(14.0);
            });
        }
        ui.collapsing(format!("Сохранённые файлы · {}", self.exported.len()), |ui| {
        for path in self.exported.clone() {
            panel(p).show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.label(
                    RichText::new(
                        PathBuf::from(&path)
                            .file_name()
                            .unwrap_or_default()
                            .to_string_lossy(),
                    )
                    .size(18.0),
                );
                ui.horizontal(|ui| {
                    if ui.button("Слушать файл").clicked() {
                        match Player::files(
                            vec![PathBuf::from(&path)],
                            PathBuf::from(&path)
                                .file_name()
                                .unwrap_or_default()
                                .to_string_lossy()
                                .into(),
                        ) {
                            Ok(player) => self.player = Some(player),
                            Err(e) => self.notice = Some((e, true)),
                        }
                    }
                    if ui.button("Открыть папку").clicked() {
                        let path = PathBuf::from(&path);
                        let _ = backend::open_folder(if path.is_dir() {
                            &path
                        } else {
                            path.parent().unwrap()
                        });
                    }
                });
            });
            ui.add_space(10.0);
        }
        });
        ui.add_space(12.0);
        ui.collapsing("Пробы звучания", |ui| {
            for job in jobs
                .iter()
                .filter(|j| j["preview"] == true && j["repair_parent"].is_null())
            {
                ui.push_id(s(job, "id"), |ui| {
                    ui.label(s(job, "title"));
                    self.job_controls(ui, job, false);
                    ui.separator();
                });
            }
        });
    }
    fn settings(&mut self, ui: &mut egui::Ui) {
        ui.set_max_width(880.0);
        let p = self.palette;
        title(ui, "Настройки", 30.0);
        ui.add_space(18.0);
        if ui.button("Словарь произношения для всех книг").clicked() {
            self.task("Открываю словарь…", Done::Rules, |api| {
                api.get("/pronunciation")
            });
        }
        ui.add_space(18.0);
        panel(p).show(ui, |ui| {
            ui.set_width(ui.available_width());
            title(ui, "Лицензия", 25.0);
            let license = &self.snapshot["license"];
            ui.label(if license["valid"] == true {
                "Лицензия активна"
            } else {
                "Лицензия требует внимания"
            });
            if !s(license, "message").is_empty() {
                ui.label(s(license, "message"));
            }
            hint(ui, p, "2 устройства · до 7 дней без интернета");
            ui.collapsing("Активировать ключ", |ui| {
                ui.add(
                    egui::TextEdit::singleline(&mut self.license_key)
                        .password(true)
                        .desired_width(420.0)
                        .hint_text("Лицензионный ключ"),
                );
                if ui.button("Активировать").clicked() {
                    let key = std::mem::take(&mut self.license_key);
                    self.post(
                        "/license/activate".into(),
                        json!({"key":key}),
                        "Проверяю лицензию…",
                        Done::Changed,
                    );
                }
            });
        });
        ui.add_space(16.0);
        panel(p).show(ui,|ui|{ui.set_width(ui.available_width());
            title(ui,"Озвучка на компьютере",25.0);ui.label("Qwen — английский и другие языки. ESpeech — русский с ударениями.");
            hint(ui,p,"Модель загружается в память при первой озвучке и остаётся доступной до закрытия программы.");
            ui.label("Папка установленной модели");ui.horizontal(|ui|{ui.add(egui::TextEdit::singleline(&mut self.model_dir).desired_width((ui.available_width()-130.0).max(200.0)));if ui.button("Выбрать…").clicked(){if let Some(path)=rfd::FileDialog::new().pick_folder(){self.model_dir=path.to_string_lossy().into();}}});
            ui.horizontal(|ui|{ui.label("Вычисления");egui::ComboBox::from_id_salt("device").selected_text(if self.device=="cuda:0"{"Видеокарта NVIDIA (CUDA)"}else{"Процессор"}).show_ui(ui,|ui|{if cfg!(target_os="windows") {ui.selectable_value(&mut self.device,"cuda:0".into(),"Видеокарта NVIDIA (CUDA)");}ui.selectable_value(&mut self.device,"cpu".into(),"Процессор");});
                egui::ComboBox::from_id_salt("backend").selected_text(match self.backend.as_str(){"cuda_graphs"=>"Ускорение NVIDIA","standard"=>"Обычный режим",_=>"Автовыбор"}).show_ui(ui,|ui|{for (mode,label) in [("auto","Автовыбор"),("cuda_graphs","Ускорение NVIDIA"),("standard","Обычный режим")]{if mode!="cuda_graphs" || cfg!(target_os="windows") {ui.selectable_value(&mut self.backend,mode.into(),label);}}});
            });
            if ui.button("Проверить модель и сохранить").clicked(){self.post("/settings/model".into(),json!({"directory":self.model_dir,"device":self.device,"backend":self.backend}),"Проверяю файлы модели…",Done::Changed);}
            if ui.button("Информация об оборудовании").clicked(){self.task("Проверяю оборудование…",Done::Hardware,|api|api.get("/hardware"));}
            if !self.hardware.is_null(){ui.collapsing("Оборудование",|ui|{for gpu in list(&self.hardware["gpus"]){ui.label(format!("{} · {:.1} ГБ видеопамяти",s(&gpu,"name"),gpu["vram_mib"].as_f64().unwrap_or(0.0)/1024.0));}hint(ui,p,s(&self.hardware,"warning"));});}
            ui.collapsing("Скачать модель",|ui|{
                hint(ui,p,"Загрузка начинается только по вашей команде. Для уже установленной модели повторная загрузка не нужна.");
                if ui.button("Проверить доступную версию и размер").clicked(){self.task("Получаю сведения о модели…",Done::Catalog,|api|api.get("/models/catalog"));}
                if let Some(catalog)=self.catalog.clone(){
                    ui.label(format!("Qwen3-TTS · {:.2} ГБ",catalog["total_bytes"].as_f64().or(catalog["bytes"].as_f64()).unwrap_or(0.0)/1e9));
                    if ui.button("Выбрать папку и скачать модель…").clicked(){if let Some(path)=rfd::FileDialog::new().pick_folder(){let bytes=catalog["total_bytes"].as_u64().or(catalog["bytes"].as_u64()).unwrap_or(0);self.post("/models/download".into(),json!({"directory":path.to_string_lossy(),"revision":catalog["revision"],"bytes":bytes,"confirmed":true}),"Начинаю загрузку…",Done::Download);}}
                }
                if ui.button("Проверить состояние загрузки").clicked(){self.task("Проверяю загрузку…",Done::Download,|api|api.get("/models/download"));}
                if !self.download.is_null(){let total=self.download["total"].as_f64().unwrap_or(1.0).max(1.0);let downloaded=self.download["downloaded"].as_f64().unwrap_or(0.0);ui.add(egui::ProgressBar::new((downloaded/total)as f32).text(format!("{:.2} / {:.2} ГБ",downloaded/1e9,total/1e9)));ui.label(match s(&self.download,"status"){"downloading"=>"Загрузка выполняется","done"=>"Модель скачана","failed"=>"Ошибка загрузки","cancelled"=>"Загрузка отменена",_=>"Загрузка не выполняется"});if !s(&self.download,"error").is_empty(){ui.label(s(&self.download,"error"));}if !s(&self.download,"directory").is_empty(){ui.label(s(&self.download,"directory"));}if s(&self.download,"status")=="downloading"&&ui.button("Отменить загрузку").clicked(){self.post("/models/download/cancel".into(),json!({}),"Отменяю загрузку…",Done::Download);}}
            });
        });
        ui.add_space(16.0);
        panel(p).show(ui, |ui| {
            ui.set_width(ui.available_width());
            title(ui, "Русская озвучка · ESpeech", 25.0);
            hint(ui, p, "Ударения расставляются автоматически. Ваши ручные пометки имеют приоритет.");
            ui.label("Папка ESpeech и вокодера");
            ui.horizontal(|ui| {
                ui.add(egui::TextEdit::singleline(&mut self.espeech_dir).desired_width((ui.available_width()-130.0).max(160.0)));
                if ui.button("Выбрать…").clicked() {
                    if let Some(path) = rfd::FileDialog::new().pick_folder() { self.espeech_dir = path.to_string_lossy().into(); }
                }
            });
            if ui.button("Проверить и включить ESpeech").clicked() {
                self.post("/settings/espeech".into(),json!({"directory":self.espeech_dir}),"Проверяю ESpeech…",Done::Changed);
            }
            hint(ui, p, "Новые русские задачи используют ESpeech. Для голоса нужна запись до 12 секунд.");
        });
        ui.add_space(16.0);
        panel(p).show(ui,|ui|{ui.set_width(ui.available_width());title(ui,concat!("Lunatica ",env!("CARGO_PKG_VERSION")),25.0);ui.label("Нативный интерфейс Rust / egui. Озвучка — Python / PyTorch / CUDA.");ui.label("Локальная студия аудиокниг");
            if ui.button("Открыть папку данных и журнал").clicked(){let _=backend::open_folder(&backend::data_dir());}
            hint(ui,p,"Книги и записи голоса хранятся локально. Лицензирование обращается к настроенному серверу.");
        });
    }
}

impl eframe::App for StudioApp {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.events();
        ctx.request_repaint_after(Duration::from_millis(100));
        if let Some(rec) = &self.recording {
            if rec.started.elapsed() > Duration::from_secs(12)
                || !ctx.input(|i| i.viewport().focused.unwrap_or(true))
                || rec.error.lock().unwrap().is_some()
            {
                self.finish_recording();
            }
        }
        if let Some(player) = &mut self.player {
            if let Err(e) = player.tick() {
                self.notice = Some((e, true));
                self.player = None;
            } else if player.finished() {
                self.player = None;
            }
        }
        #[cfg(target_os = "windows")]
        let previous_dark = self.prefs.dark;
        self.top(ctx);
        #[cfg(target_os = "windows")]
        if previous_dark != self.prefs.dark {
            crate::taskbar::set_caption(_frame, self.palette.bg, self.palette.text);
        }
        let p = self.palette;
        egui::TopBottomPanel::bottom("status-player")
            .frame(
                egui::Frame::new()
                    .fill(p.surface)
                    .inner_margin(egui::Margin::symmetric(28, 12)),
            )
            .show(ctx, |ui| {
                if self.player.is_some() {
                    let mut stop = false;
                    ui.horizontal(|ui| {
                        let player = self.player.as_mut().unwrap();
                        if ui
                            .button(if player.sink.is_paused() {
                                "Продолжить"
                            } else {
                                "Пауза"
                            })
                            .clicked()
                        {
                            if player.sink.is_paused() {
                                player.sink.play();
                            } else {
                                player.sink.pause();
                            }
                        }
                        if ui.button("Стоп").clicked() {
                            stop = true;
                        }
                        ui.label(&player.label);
                    });
                    if stop {
                        self.player = None;
                    }
                    ui.separator();
                }
                if let Some((message, error)) = self.notice.clone() {
                    ui.horizontal_wrapped(|ui| {
                        ui.colored_label(
                            if error {
                                if self.prefs.dark {
                                    c(0xffb5ad)
                                } else {
                                    c(0x9d3028)
                                }
                            } else {
                                p.text
                            },
                            message,
                        );
                        if ui.small_button("Скрыть").clicked() {
                            self.notice = None;
                        }
                    });
                }
                ui.horizontal(|ui| {
                    if !self.busy.is_empty() {
                        ui.spinner();
                        ui.label(&self.busy);
                    } else {
                        hint(
                            ui,
                            p,
                            if self.api.is_some() {
                                "Локальная библиотека"
                            } else {
                                "Запуск модуля озвучки…"
                            },
                        );
                    }
                    ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                        hint(
                            ui,
                            p,
                            if s(&self.snapshot["settings"], "device") == "cuda:0" {
                                "NVIDIA CUDA · на вашем компьютере"
                            } else {
                                "На вашем компьютере"
                            },
                        );
                    });
                });
            });
        egui::CentralPanel::default()
            .frame(
                egui::Frame::new()
                    .fill(p.bg)
                    .inner_margin(egui::Margin::symmetric(24, 24)),
            )
            .show(ctx, |ui| {
                if self.api.is_none() {
                    if self.launch_failed {title(ui,"Не удалось открыть модуль озвучки",30.0);ui.label("Проверьте сообщение внизу окна. Закройте другую копию программы, если она открыта.");if ui.button("Повторить запуск").clicked(){self.launch_failed=false;self.notice=None;self.started=Instant::now();let tx=self.tx.clone();let context=ctx.clone();std::thread::spawn(move||{let _=tx.send(Event::Ready(Api::start()));context.request_repaint();});}return;}
                    ui.add_space(80.0);
                    title(ui, "Lunatica", 40.0);
                    ui.add_space(20.0);
                    ui.spinner();
                    ui.label(format!(
                        "Открываю библиотеку и модуль озвучки… {} с",
                        self.started.elapsed().as_secs()
                    ));
                    hint(ui, p, "Первый запуск может занять до двух минут.");
                    return;
                }
                egui::ScrollArea::vertical()
                    .id_salt(match self.page {
                        Page::Home => "home",
                        Page::Studio => "studio",
                        Page::Voices => "voices",
                        Page::Exports => "exports",
                        Page::Settings => "settings",
                    })
                    .show(ui, |ui| {
                        ui.set_width(ui.available_width());
                        ui.add_enabled_ui(self.busy.is_empty(), |ui| match self.page {
                            Page::Home => self.home(ui),
                            Page::Studio => self.studio(ui),
                            Page::Voices => self.voices(ui),
                            Page::Exports => self.exports(ui),
                            Page::Settings => self.settings(ui),
                        });
                    });
            });
        self.pronunciation_windows(ctx);
        if self.prepared.is_some() {
            let mut open = true;
            egui::Window::new("Текст, который услышит модель")
                .open(&mut open)
                .default_width(720.0)
                .show(ctx, |ui| {
                    egui::ScrollArea::vertical()
                        .max_height(650.0)
                        .show(ui, |ui| {
                            ui.label(
                                RichText::new(self.prepared.as_ref().unwrap()).font(serif(19.0)),
                            );
                        });
                });
            if !open {
                self.prepared = None;
            }
        }
        if self.delete_confirm {
            egui::Window::new("Удалить книгу?").collapsible(false).resizable(false).anchor(egui::Align2::CENTER_CENTER,Vec2::ZERO).show(ctx,|ui|{
            ui.label("Книга и её задачи будут удалены из библиотеки.\nИсходный файл, голоса и сохранённые MP3 останутся.");ui.horizontal(|ui|{
                if ui.button("Отмена").clicked(){self.delete_confirm=false;}
                if ui.button("Удалить из библиотеки").clicked(){self.delete_confirm=false;let route=format!("/projects/{}",self.prefs.book);self.task("Удаляю книгу…",Done::Deleted,move|api|api.delete(&route));}
            });
        });
        }
        if ctx.input(|i| i.viewport().close_requested()) && !self.dirty.is_empty() {
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.notice = Some((
                "Есть несохранённые правки. Сохраните или отмените их перед закрытием".into(),
                true,
            ));
            self.page = Page::Studio;
        }
    }
}
impl Drop for StudioApp {
    fn drop(&mut self) {
        self.recording = None;
        self.player = None;
        self.persist();
    }
}
