> **0.4.0 更新（2026-10-20）：** 原有声场、字幕和会话界面继续使用；设置面板现已增加 Drama 3 / Qwen3-TTS Base 选择、参考录音导入及已保存音色选择。下文保留早期设计与演练过程，早期截图和测试次数不代表本版最终验收；当前执行结果见 [VALIDATION.md](VALIDATION.md)。

# HER Voice — interface design

## Scope and reference

HER Voice is an independent Windows-friendly browser interface. It uses JOSIE's
public interaction ideas: a central voice visualization, readable live captions,
sparse call controls, and optional conversation/settings panels. It does not use
or claim access to the private JOSIE Swift application.

Public references inspected on 2026-10-20:

- <https://github.com/Goekdeniz-Guelmez/JOSIE-Voice/blob/main/README.md>
- <https://github.com/Goekdeniz-Guelmez/JOSIE-Voice/blob/main/docs/images/mac-call-orb-1600.webp>

The public README identifies its application screenshots and clips as mockups
and says the repository hosts the website and visuals while app source is
private. Those images establish interaction inspiration, not verified runtime
behavior or reusable source code.

## Visual specification

A complete desktop concept was generated with the built-in image tool before
implementation. The selected concept is 1536 × 1024. Concept and QA imagery are
kept outside the distributable project. The UI itself is HTML, CSS and ES modules;
no screenshot is used as an interactive surface.

The screen is a quiet, open charcoal canvas. A fine silver/aqua sound field is
the main visual focus. It reacts to real input/output levels supplied by the
application. The waveform is a functional, code-rendered visualization, not a
photo, a downloaded JOSIE orb, or a generated raster pretending to be live audio.

### Tokens

| Token | Value / use |
| --- | --- |
| Background | `#080d11`, neutral charcoal with a slight cool cast |
| Surface | `#121b21`, controls and drawers |
| Surface raised | `#1a262e` |
| Text | `#edf0f1` |
| Muted text | `#a8b4bd` |
| Secondary text | `#7f919e` |
| Border | `#30404b`, brighter on interactive hover |
| Accent | `#b9dedb`, sound visualization and focus |
| Primary button | `#eeefeb` with `#101a20` text |
| Demo / paused | `#e2c185` |
| Error | `#f3a8a2` |
| Type | Segoe UI Variable, Segoe UI, Microsoft JhengHei, system sans |
| UI text | 13–16 px, explicit control typography |
| Main caption | 24–34 px desktop; 22–26 px phone |
| Icons | Consistent rounded SVG outlines, 1.65 px stroke |
| Spacing | 4, 8, 12, 16, 24, 32, 48 px |
| Containers | Open stage, one control row, optional modal side drawer |
| Motion | Slow sound-field movement; short state transitions; no motion when reduced-motion is requested |

### Primary-screen copy lock

- Wordmark: `HER` / `VOICE`
- Header: `未連線`, `會話`, `設定`
- Idle heading: `準備好聽你說。`
- Idle supporting text: `從一個念頭，開始一段對話。`
- Controls: `麥克風`, `字幕`, `開始對話`, `聲音`, `輸入`
- Footer: `本機聽說 · DeepSeek 對話`, `試用示範`

State-dependent copy is intentional functional content: connection progress,
listening/thinking/speaking/paused/interrupted states, real transcripts, real
errors, explicit demo disclosure, interruption control, and measured first-audio
time when the application provides it. The measurement is labelled
`事件至播放約 … 秒`: it is an event-to-scheduled-playback measurement, not a
mouth-to-ear latency claim. These are not added marketing claims.

### Layout and state inventory

1. Header: typographic wordmark, connection state, conversation and settings.
2. Open stage: live sound-field canvas, narrow waveform, user caption and current
   assistant caption/state. Captions can be hidden without ending the call.
3. Control row: pause/resume microphone, captions, start/end conversation,
   output-device settings, text composer.
4. Optional composer: text entry with Enter to submit and Shift+Enter for newline.
5. Footer: current architecture/mode disclosure and explicit live/demo switch.
6. Conversation drawer: current messages, per-message interrupted state, and
   `清除並重設對話`. Clearing ends the connection and resets the conversation.
   It does not claim durable memory or exact heard-history truncation.
7. Settings drawer: actual device choices supplied by the application, captions,
   barge-in, and read-only service status. API keys are not entered or exposed here.

At phone widths, header labels simplify, sound-field dimensions shrink, the
five controls remain reachable, and drawers become almost full-width. At short
viewport heights the page can scroll; controls and captions are not clipped.
The measured height of the composer and extra caption lines reduces the
sound-field height. Opening text entry therefore preserves room for the header,
controls and footer rather than simply adding another block below a fixed orb.

## UI boundary

`ui.js` exports `createUI(actions)`, returning `render(snapshot)` and
`setLevels(input, output)`. The UI owns only presentational state such as open
drawers and the text composer. The app owns audio, networking, response IDs,
interruptions, connection mode, messages, and service health.

Callbacks: `onStart`, `onStop`, `onPause`, `onInterrupt`, `onSend(text)`,
`onModeChange(mode)`, `onSettingsChange(partialSettings)`, `onClearHistory`.

No WebSocket, microphone access, API credential, fake conversation or automatic
demo is implemented inside the UI. Demo fixtures, when used during development,
are kept outside production files. The visible demo disclosure remains present
through every demo state, including connected and speaking.

Pause and manual interruption use the application controller's new-session
boundary. While paused, the connection label says `已暫停`, not `已連線`, and
text submission is disabled. The user is told completed turns will be retained
and partial speech must be repeated after resuming. Demo instructions describe
typing and clicking interruption, never imply the microphone is listening.

### Accessibility

- Semantic buttons, forms, native dialogs and labelled selects.
- Visible focus rings; focus returns to the drawer/composer trigger.
- State announcements use a polite live region without reading every partial
  transcript token aloud.
- Space pauses/resumes only outside editable/focusable controls. Escape closes
  an open overlay/composer first, otherwise requests interruption during a reply.
- Reduced motion draws a stable sound field and removes transition movement.
- Connection, demo, paused and error states are named in text as well as colour.
- Transcript and error strings are inserted with `textContent`.

## Verification ledger

Reviewed on 2026-10-20. The generated concept and fresh browser screenshots were
opened together at their original resolution. The concept and main desktop
captures are 1536 × 1024. Additional renders are 1366 × 768, 390 × 844 and
320 × 700, including an open text composer. No screenshot is bundled as UI code.

| Comparison | Finding and disposition |
| --- | --- |
| Canvas and palette | Both use an open charcoal canvas, pale primary text and silver/aqua accents. The implementation uses the specified `#080d11` background; it keeps the centre and side margins free of decorative panels. |
| Header and footer placement | At 1536 px wide, the wordmark and footer begin about 52 px from the left, matching the concept. Connection, conversation and settings remain at the upper right. State-dependent amber demo text is an intentional addition. |
| Sound-field shape | The first render showed crossed front/back latitude lines and was too dim. The revised canvas draws the visible surface, uses clearer contours and restrained local glow. It preserves the concept's central organic form and dark centre. Its flowing geometry is more regular and its glow softer than the generated concept; the live code-rendered field is not claimed as a pixel-identical raster recreation. |
| Caption hierarchy | A large centred main line, smaller muted supporting line and a narrow waveform preserve the concept's hierarchy. User text, partial assistant captions and event-to-playback timing have explicit positions. Long assistant text is contained and scrollable rather than expanding without a bound. |
| Five-control row | Four outlined circular controls flank the pale pill action in the same order as the concept. Desktop circles are 72 px and the main action 246 × 74 px, slightly more compact than the concept. Active calls use a restrained red end action. All five controls remain in one row at 320 px. |
| Composer and active states | The initial speaking/paused render exceeded the desktop viewport and could scroll the header away. Measuring composer/caption space and shrinking the sound field fixed this. The revised speaking, paused and interrupted screenshots show header, captions, composer, controls and footer together at 1536 × 1024. |
| Responsive layout | The 390 × 844 and 320 × 700 screenshots keep the composer and all controls within the available width. Header labels simplify, the field shrinks and the 320 px footer stacks. The 1366 × 768 screenshot also retains the full control row and footer. No horizontal overflow was detected. |
| Drawers, focus and reduced motion | The settings screenshot uses the same surfaces and type hierarchy, with readable wrapped profile text and an accessible close control. Visible keyboard focus appears in the responsive captures. The browser context requested reduced motion; the static sound field remains complete and readable. Native dialog Escape dismissal passed. |

### Exact-copy review

The static live-idle strings in `index.html` and `STATE_COPY.idle` match the
primary-screen copy lock above. The browser smoke run uses explicit demo mode,
so its heading, action, connection label and footer intentionally differ:
`先感受對話的節奏。`, `開始示範`, `示範待命` and
`示範演練 · 測試音訊`. Every demo state retains the disclosure
`示範模式 · 協定演練／測試音，不使用麥克風或 API`.
Speaking, interrupted and paused copy describes protocol rehearsal and typing;
it does not invite the user to speak into a disabled microphone.

### Browser evidence and limits

The local real application and demo WebSocket were exercised in Google Chrome
headless 155.0.8059.84 after the configured browser surfaces could not reach the
local service. An isolated CJK font configuration was used for Linux QA; no
test font or browser is a production dependency. `scripts/browser-check.mjs`
reported seven passing checks and an empty console/page-error list:

- Explicit demo disclosure with no microphone/API request.
- Streaming typed turn and manual interruption across session reset.
- Return to listening after scheduled audio finishes.
- Pause/resume and completed-text history restoration.
- Caption toggle, settings drawer and Escape dismissal.
- Clear-history reset and disconnection.
- Three responsive viewports without horizontal overflow.

The inspected captures are `desktop-idle.png`, `desktop-speaking.png`,
`desktop-paused.png`, `desktop-interrupted.png`, `desktop-settings.png`, and
the three `viewport-…` captures. They are QA intermediates outside the project.
`ui.js` also passed Node's ES-module syntax check. These checks establish UI and
demo-protocol behaviour; they do not establish Windows microphone behaviour,
acoustic echo cancellation, local ASR/TTS inference performance or model quality
on the user's hardware.

Intentional differences from a static concept:

- Sound-field lines vary with audio and time rather than being a fixed raster.
- Demo and error disclosures appear only in their real application states.
- Drawers, device selectors, keyboard entry and mobile layout use the same token
  system while providing the functional states that a single idle image cannot
  display.
