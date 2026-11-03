//! Windows keeps a separate large icon for the taskbar and Alt+Tab.
#[cfg(target_os = "windows")]
pub fn set_icon(cc: &eframe::CreationContext<'_>) {
    use raw_window_handle::{HasWindowHandle, RawWindowHandle};
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    extern "system" {
        fn GetModuleHandleW(name: *const u16) -> *mut c_void;
    }
    #[link(name = "user32")]
    extern "system" {
        fn LoadImageW(
            instance: *mut c_void,
            name: *const u16,
            kind: u32,
            cx: i32,
            cy: i32,
            flags: u32,
        ) -> *mut c_void;
        fn SendMessageW(hwnd: isize, message: u32, wparam: usize, lparam: isize) -> isize;
    }
    let Ok(handle) = cc.window_handle() else {
        return;
    };
    let RawWindowHandle::Win32(window) = handle.as_raw() else {
        return;
    };
    // winresource embeds icon resource 1. LR_SHARED keeps its lifetime with
    // the executable, so Windows can use it until the window is destroyed.
    unsafe {
        let icon = LoadImageW(
            GetModuleHandleW(std::ptr::null()),
            1usize as *const u16,
            1,
            48,
            48,
            0x8000,
        );
        if !icon.is_null() {
            SendMessageW(window.hwnd.get(), 0x0080, 1, icon as isize);
        }
    }
}

#[cfg(not(target_os = "windows"))]
pub fn set_icon(_cc: &eframe::CreationContext<'_>) {}
