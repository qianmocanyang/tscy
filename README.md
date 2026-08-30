# 游戏实时同声传译（tscy）

说话 → 自动判断是**中/英/韩/日/俄**哪一种 → 翻译成你指定的语言 → 以**游戏内置字幕 + 语音**返回。
全程由自定义按键驱动，不占用鼠标、不打断操作。

---

## 30 秒上手

```bat
install.bat              :: 1. 自动选解释器 + 建虚拟环境 + 装依赖
.venv\Scripts\python.exe main.py --selftest   :: 2. 自检
右键 run.bat → 以管理员身份运行               :: 3. 开玩
```

> ⚠️ **必须用管理员权限运行**，否则全局热键不生效（游戏里按键没反应）。

---

## 默认按键

| 按键 | 作用 |
|---|---|
| **按住 F9** | 按住说话，松开即翻译输出 |
| **F10** | 两步模式下：把识别出的原文翻译并输出 |
| **F11** | 丢弃当前这一段 |
| `Ctrl+Alt+L` | 循环切换目标语言（中→英→韩→日→俄） |
| `Ctrl+Alt+O` | 显示/隐藏字幕 |
| `Ctrl+Alt+S` | 开关语音播报 |
| `Ctrl+Alt+Q` | 退出 |

---

## 三种触发模式

改 `config/config.json` 里的 `mode`（改完自动生效，不用重启）：

| 模式 | 值 | 怎么玩 | 适合 |
|---|---|---|---|
| **两步式**（默认） | `two_step` | 按 F9 开始录音 → 再按 F9 停 → 按 F10 才翻译输出 | 想先确认识别对不对再翻 |
| 按住说话 | `ptt` | 按住 F9 说话，松手立刻翻 | **竞技游戏推荐**，延迟最低 |
| 自动监听 | `auto` | 不用按键，检测到说话自动收音 | 挂机聊天、看外语直播 |

**两步模式**就是你描述的"识别到语言后，按键返回设定语言"：识别完先把**原文**显示在字幕上，
你确认没听错，再按 F10 翻译。游戏里想要"说完立刻出结果"，把 `mode` 改成 `ptt` 即可。

---

## 常用命令

```bash
python main.py --list-devices              # 列出麦克风，挑一个填进 config
python main.py --target ko                 # 临时把目标语言改成韩语
python main.py --mode two_step             # 临时改用两步式
python main.py --model small               # 换更准的模型（更慢）
python main.py --no-tts                    # 只要字幕，不朗读
python main.py --selftest --with-model     # 自检 + 预先下载模型
```

---

## 网络环境说明（国内用户必读）

实测成都家用宽带下：

| 服务 | 状态 |
|---|---|
| HuggingFace 官方站 | ❌ 连不上 → **程序自动切 `hf-mirror.com` 镜像下载模型** |
| Google 翻译 | ❌ 超时 |
| 有道 / 微软翻译 | ❌ 超时 |
| **MyMemory** | ✅ 可用（免费后端，开箱即用） |
| **DeepL** | ✅ 可达（需 Key，质量最好） |

翻译后端默认设为 `auto`：启动时会挨个试，自动挑一个真正连通的。想要更好的质量就去
[DeepL 申请免费 Key](https://www.deepl.com/pro-api)（50 万字/月），写进 `config/secrets.json`：

```json
{
  "deepl_key": "你的key:fx"
}
```

程序会自动优先用它。

---

## 目录

```
config/config.json     主配置（改热键、目标语言、字幕样式都在这）
config/secrets.json    API Key（自己建，已 gitignore）
docs/技术设计文档.md    完整的架构设计与选型理由
models/                Whisper 模型缓存
cache/                 翻译缓存 + 语音缓存
logs/tscy.log          运行日志
```

---

## 调参速查

| 症状 | 改什么 |
|---|---|
| 游戏音效老是误触发录音 | `vad.start_db` 调高（-40 → -35），或直接用 PTT 模式 |
| 说话慢，老是被截断 | `vad.silence_ms` 调大（600 → 900） |
| 识别不准 | `asr.model_size` 换成 `small`，或启用 `asr.backend: qwen` |
| 太卡、掉帧 | `asr.model_size` 换成 `tiny`，或关掉语音 `output.speech` |
| 翻译慢 | 换 DeepL Key，或提高 `translate.cache_size` |
| 字幕挡视线 | 调 `overlay.y`（0~1，越大越靠下）、`overlay.width_ratio` |
| 语音想更自然 | `output.tts_engine` 改成 `qwen`（需 DashScope Key） |

改 `config/config.json` 保存后**立即生效**，不用重启程序。

---

## 两种发行形态

### 形态 A：单 exe 直接打开

```bash
python build_onefile.py
```

产物 `dist/tscy.exe`，把它和 `assets/` 一起发给用户。双击即运行，
首次启动会在 exe 旁边自动生成 `config/`、`cache/`、`logs/`、`models/`。

### 形态 B：安装包

#### 方式 1：一键安装脚本（已提供）

解压 `dist/tscy_portable.zip` 后，右键 `tscy_setup.bat` → 以管理员身份运行：

- 自动复制 `tscy.exe` + `assets` 到 `%LOCALAPPDATA%\tscy`
- 创建桌面 + 开始菜单快捷方式
- 用户数据（config/cache/logs/models）放在 `%LOCALAPPDATA%\tscy`，无 UAC 烦恼

#### 方式 2：标准安装包（可自行编译）

如需更标准的 `.exe` 安装包：

1. 先执行形态 A 生成 `dist/tscy.exe`。
2. 安装 [Inno Setup](https://jrsoftware.org/isinfo.php)。
3. 打开 `build_installer.iss` 点击 Compile，生成 `dist/tscy_setup_v1.0.exe`。

`build_installer.iss` 已配置好：安装到 `Program Files\tscy`、桌面/开始菜单快捷方式、
用户数据自动放到 `%LOCALAPPDATA%\tscy`。

---

## 千问 (DashScope) 配置

在 `config/secrets.json` 写入：

```json
{
  "qwen_key": "sk-你的DashScopeKey"
}
```

然后在设置面板或 `config/config.json` 里切换：

```json
{
  "asr": { "backend": "qwen" },
  "output": { "tts_engine": "qwen" }
}
```

> 默认仍以本地 Whisper + edge-tts 为主（速度最快、免费、离线）。
> 千问作为可选增强，识别率和音质更好，但需要联网和 Key。
