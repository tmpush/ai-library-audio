/// Exclude the heap from automatic Windows Error Reporting collection.
/// Privileged manual memory capture remains possible.
#[cfg(target_os = "windows")]
pub fn restrict_crash_reports() {
    #[link(name = "kernel32")]
    extern "system" {
        fn WerSetFlags(flags: u32) -> i32;
    }
    let result = unsafe { WerSetFlags(1) };
    if result < 0 {
        eprintln!("Windows crash-report privacy setting failed: {result}");
    }
}

#[cfg(not(target_os = "windows"))]
pub fn restrict_crash_reports() {}
