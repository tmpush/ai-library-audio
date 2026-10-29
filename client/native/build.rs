fn main() {
    if cfg!(target_os = "windows") {
        winresource::WindowsResource::new()
            .set_icon("assets/icon.ico")
            .set("ProductName", "Книжная мастерская")
            .set(
                "FileDescription",
                "Книжная мастерская — нативная студия аудиокниг",
            )
            .set("LegalCopyright", "Local Books Studio")
            .compile()
            .expect("Windows resources");
    }
    println!("cargo:rerun-if-env-changed=BOOKS_LICENSE_URL");
    println!("cargo:rerun-if-env-changed=BOOKS_LICENSE_PUBLIC_KEY");
}
