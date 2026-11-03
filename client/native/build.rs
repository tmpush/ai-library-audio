fn main() {
    println!("cargo:rerun-if-changed=assets/icon.ico");
    if cfg!(target_os = "windows") {
        winresource::WindowsResource::new()
            .set_icon("assets/icon.ico")
            .set("ProductName", "Lunatica")
            .set(
                "FileDescription",
                "Lunatica — нативная студия аудиокниг",
            )
            .set("LegalCopyright", "Lunatica")
            .compile()
            .expect("Windows resources");
    }
    println!("cargo:rerun-if-env-changed=BOOKS_LICENSE_URL");
    println!("cargo:rerun-if-env-changed=BOOKS_LICENSE_PUBLIC_KEY");
}
