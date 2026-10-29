use reqwest::blocking::{multipart, Client, Response};
use serde_json::{json, Value};
use std::os::windows::process::CommandExt;
use std::{
    fs::{self, File},
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};

pub fn data_dir() -> PathBuf {
    PathBuf::from(std::env::var_os("APPDATA").expect("APPDATA")).join("app.localbooks.studio")
}
pub struct Engine(pub Child);
impl Drop for Engine {
    fn drop(&mut self) {
        if let Some(mut input) = self.0.stdin.take() {
            let _ = writeln!(input, "shutdown");
        }
        let until = Instant::now() + Duration::from_secs(3);
        while Instant::now() < until {
            if self.0.try_wait().ok().flatten().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
#[derive(Clone)]
pub struct Api {
    client: Client,
    base: String,
    token: String,
}
impl Api {
    pub fn start() -> Result<(Self, Engine), String> {
        let root = data_dir();
        fs::create_dir_all(&root).map_err(|e| e.to_string())?;
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let engine_path = std::env::var_os("BOOKS_ENGINE_PATH")
            .map(PathBuf::from)
            .unwrap_or_else(|| exe.parent().unwrap().join("engine/books-engine.exe"));
        let token = format!(
            "{}{}",
            uuid::Uuid::new_v4().simple(),
            uuid::Uuid::new_v4().simple()
        );
        let mut command = Command::new(&engine_path);
        command
            .env("BOOKS_DATA", &root)
            .env("BOOKS_LOCAL_TOKEN", &token)
            .env(
                "BOOKS_LICENSE_URL",
                option_env!("BOOKS_LICENSE_URL").unwrap_or("http://127.0.0.1:8791"),
            )
            .env(
                "BOOKS_LICENSE_PUBLIC_KEY",
                option_env!("BOOKS_LICENSE_PUBLIC_KEY").unwrap_or(""),
            )
            .creation_flags(0x08000000)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(File::create(root.join("engine.log")).map_err(|e| e.to_string())?);
        let mut engine = Engine(
            command
                .spawn()
                .map_err(|e| format!("Не удалось запустить модуль озвучки: {e}"))?,
        );
        let output = engine.0.stdout.take().unwrap();
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(output).lines().map_while(Result::ok) {
                if let Ok(v) = serde_json::from_str::<Value>(&line) {
                    if v["type"] == "ready" {
                        let _ = tx.send(v["port"].as_u64().unwrap_or(0));
                    }
                }
            }
        });
        let port = rx
            .recv_timeout(Duration::from_secs(120))
            .map_err(|_| "Модуль озвучки не ответил. Подробности в engine.log".to_string())?;
        if port == 0 {
            return Err("Неверный адрес модуля озвучки".into());
        }
        let client = Client::builder()
            .timeout(Duration::from_secs(600))
            .no_proxy()
            .build()
            .map_err(|e| e.to_string())?;
        Ok((
            Self {
                client,
                base: format!("http://127.0.0.1:{port}"),
                token,
            },
            engine,
        ))
    }
    fn decode(response: Result<Response, reqwest::Error>) -> Result<Value, String> {
        let response = response.map_err(|e| {
            if e.is_connect() {
                "Модуль озвучки недоступен. Перезапустите программу".into()
            } else {
                format!("Ошибка обращения к модулю озвучки: {e}")
            }
        })?;
        let status = response.status();
        let value: Value = response
            .json()
            .map_err(|_| "Не удалось прочитать ответ модуля озвучки".to_string())?;
        if !status.is_success() {
            return Err(value["detail"]
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| format!("Ошибка {status}: {}", value["detail"])));
        }
        Ok(value)
    }
    pub fn get(&self, path: &str) -> Result<Value, String> {
        Self::decode(
            self.client
                .get(format!("{}{path}", self.base))
                .bearer_auth(&self.token)
                .timeout(Duration::from_secs(15))
                .send(),
        )
    }
    pub fn post(&self, path: &str, value: &Value) -> Result<Value, String> {
        Self::decode(
            self.client
                .post(format!("{}{path}", self.base))
                .bearer_auth(&self.token)
                .json(value)
                .send(),
        )
    }
    pub fn delete(&self, path: &str) -> Result<Value, String> {
        Self::decode(
            self.client
                .delete(format!("{}{path}", self.base))
                .bearer_auth(&self.token)
                .send(),
        )
    }
    pub fn upload(
        &self,
        path: &str,
        filename: String,
        bytes: Vec<u8>,
        fields: Vec<(&str, String)>,
    ) -> Result<Value, String> {
        let mut form =
            multipart::Form::new().part("file", multipart::Part::bytes(bytes).file_name(filename));
        for (k, v) in fields {
            form = form.text(k.to_owned(), v);
        }
        Self::decode(
            self.client
                .post(format!("{}{path}", self.base))
                .bearer_auth(&self.token)
                .multipart(form)
                .send(),
        )
    }
    pub fn snapshot(&self) -> Result<Value, String> {
        Ok(
            json!({"projects":self.get("/projects")?,"voices":self.get("/voices")?,
            "jobs":self.get("/jobs")?,"settings":self.get("/settings")?,"license":self.get("/license")?}),
        )
    }
}
pub fn open_folder(path: &Path) -> Result<(), String> {
    Command::new("explorer.exe")
        .arg(path)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}
