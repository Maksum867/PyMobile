# Your first PyMobile app, step by step

Fifteen minutes, no Android device, no Java. You will end with a running desktop
preview and an installable APK.

Every command is a shell command. Every code block is a whole file — copy it in
and it runs.

---

## 0. What you need

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install pymobile
python -m pymobile --version     # 0.15.1
```

Nothing else, for now. The SDK comes in step 6; the desktop preview runs without
it.

---

## 1. Create a project

```bash
python -m pymobile init --name Counter my-counter
cd my-counter
```

You get:

```
my-counter/
├── main.py            ← your app
├── pymobile.toml      ← settings (name, permissions, target Android level)
├── assets/            ← images, fonts, data files
└── README.md
```

`main.py` already runs. Look at it before changing anything.

---

## 2. Run it on your computer

```bash
python -m pymobile run
```

A window opens. This is the **same widget code** the phone will run — not a mock.
While it is open, editing `main.py` re-renders the window (hot reload).

Prefer a browser? `python -m pymobile preview` prints a local URL.

---

## 3. A screen is a class with a `build()`

Replace the contents of `main.py` with this:

```python
from pymobile import App, Button, Column, Label, Screen


class Home(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def build(self) -> Column:
        return Column(
            Label(f"Count: {self.count}"),
            Button("Add one", on_press=self.add),
        )

    def add(self) -> None:
        self.count += 1
        self.refresh()          # rebuild this screen


app = App("Counter")
app.run(Home())
```

Three things are worth stopping on:

- **`super().__init__()`** — without it you get a message that says exactly
  that, not a crash deep inside the framework.
- **`on_press=self.add`, not `self.add()`** — with the parentheses the handler
  runs once, now, and the button does nothing. PyMobile warns about this.
- **`self.refresh()`** — you say when to redraw. There is no hidden diffing of
  your variables.

If the window does not update, you forgot the `refresh()`.

---

## 4. Add a second screen and go back

Screens are pushed on a stack. The system back gesture (and the browser back
button) pops it.

```python
from pymobile import App, Button, Column, Label, Screen, TextInput


class Welcome(Screen):
    def build(self) -> Column:
        return Column(
            Label("What should we call you?"),
            Button("Continue", on_press=self.go),
        )

    def go(self) -> None:
        self.app.push(NameScreen())


class NameScreen(Screen):
    def __init__(self) -> None:
        super().__init__()
        self.name = TextInput(id="name", placeholder="your name")

    def build(self) -> Column:
        return Column(
            self.name,
            Button("Save", on_press=self.save),
        )

    def save(self) -> None:
        self.app.storage.set("name", self.name.value)
        self.app.pop()


app = App("Hello")
app.run(Welcome())
```

`self.app.storage` is a small JSON store that survives the app being closed.
`self.app.push()` / `self.app.pop()` change screens; `self.app.reset(screen)`
replaces the whole stack.

---

## 5. Save data that must not be lost

```python
app = App("Notes")
app.storage.set("theme", "dark")        # any JSON-serialisable value
theme = app.storage.get("theme", "light")
```

For a list of records (notes, tasks, messages) use `ModelStore`, which adds
ids, queries and migrations instead of one big dictionary:

```python
from dataclasses import dataclass
from pymobile import ModelStore


@dataclass
class Note:
    id: str
    text: str


notes = ModelStore(app.storage, Note, key="notes")
notes.put(Note(id="1", text="buy milk"))
for note in notes.find(lambda n: "milk" in n.text):
    print(note.text)
```

Secrets (a token, a password) do not go in either — use
`app.save_secret("token", value)`, which stores them encrypted with a key the
Android keystore holds.

---

## 6. Build a real APK

```bash
python -m pymobile doctor       # what is missing, if anything
python -m pymobile setup-sdk    # downloads the Android SDK (~1 GB, once)
python -m pymobile build --native
```

Result:

```
✓ built my-counter-0.1.0.apk — 16.4 MB, installable
```

Then, with a phone plugged in over USB (developer mode on):

```bash
python -m pymobile install
```

`adb logcat -s pymobile` shows your `print()` output and tracebacks.

Uploading to Google Play needs an `.aab` instead of an APK:

```bash
python -m pymobile build --native --bundle
```

That writes both, and checks the bundle with bundletool.

---

## 7. When something goes wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| `'Home' object has no attribute '_root'` | missing `super().__init__()` | add it as the first line of `__init__` |
| Button does nothing | `on_press=self.tap()` | drop the parentheses |
| Screen does not update | nothing called `refresh()` | call it after changing state |
| `WidgetTypeError` | a string where a widget belongs | wrap it: `Label("text")` |
| `python -m pymobile run` prints a traceback | see the file and line it names | `python -m pymobile check` runs the same check without launching |
| APK installs, then closes at once | the startup check would have caught it | `python -m pymobile build --native -v` and read the first error |

The three most common beginner mistakes have their own page:
[BEGINNER-MISTAKES.md](BEGINNER-MISTAKES.md).

---

## Where to go next

- **Every widget** — `python -m pymobile preview --text` prints a screen as
  text, and `python -m pymobile preview --png screen.png` draws it as a picture,
  so you can check a layout without a phone.
- **The whole app in one file** — steps 3–5 above are the complete program; keep
  them together and you have a note list that survives a restart.
- **The reference** — the README's widget and API sections.
- **Stuck?** — open a discussion with the output of `python -m pymobile doctor`.
