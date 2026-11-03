#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
mod audio;
mod backend;
mod studio;
mod taskbar;

use fs2::FileExt;
fn main() -> eframe::Result {
    let root = backend::data_dir();
    std::fs::create_dir_all(&root).expect("Application data directory");
    let lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(root.join("native-ui.lock"))
        .expect("Application lock");
    if lock.try_lock_exclusive().is_err() {
        rfd::MessageDialog::new()
            .set_title("Lunatica")
            .set_description("Программа уже открыта. Выберите её окно на панели задач.")
            .show();
        return Ok(());
    }
    let options = eframe::NativeOptions {
        viewport: eframe::egui::ViewportBuilder::default()
            .with_inner_size([1360.0, 920.0])
            .with_min_inner_size([1000.0, 720.0])
            .with_icon(
                eframe::icon_data::from_png_bytes(include_bytes!("../assets/128x128.png"))
                    .expect("Icon"),
            ),
        ..Default::default()
    };
    eframe::run_native(
        "Lunatica",
        options,
        Box::new(|cc| {
            taskbar::set_icon(cc);
            Ok(Box::new(studio::StudioApp::new(cc)))
        }),
    )
}
