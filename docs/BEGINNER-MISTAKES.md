# The five mistakes everyone makes in the first hour

Each of these is a real message from a real app. PyMobile tries to name the
mistake instead of showing you a Python internals traceback — this page is the
long version of those messages.

---

## 1. Forgetting `super().__init__()`

```python
class Home(Screen):
    def __init__(self):
        self.title = "Home"        # ✗ Screen.__init__ never ran
```

You get:

```
RuntimeError: Home.__init__ did not call super().__init__()
  hint: add `super().__init__()` as the first line of Home.__init__
```

**Fix.** Always the first line:

```python
class Home(Screen):
    def __init__(self):
        super().__init__()         # ✓
        self.title = "Home"
```

Same for a widget you subclass. If a class has no `__init__` of its own, there is
nothing to fix.

---

## 2. Calling the handler instead of passing it

```python
Button("Save", on_press=self.save())      # ✗ runs save() now, then passes None
Button("Save", on_press=self.save)        # ✓
```

The symptom: the action happens once, when the screen is built (often during
startup, before anything is on screen), and the button then does nothing at all.

The build warns about this — `on_press=self.save()` means "the handler ran once
at build time". If `save()` needs an argument, use a lambda:

```python
Button("Delete", on_press=lambda: self.delete(item))
```

---

## 3. Changing state and expecting the screen to notice

```python
def add(self):
    self.count += 1        # ✗ the window still shows the old number
```

PyMobile does not compare your attributes on a timer. You say when to redraw:

```python
def add(self):
    self.count += 1
    self.refresh()         # ✓
```

If several things change together, change them all, then call `refresh()` once.

For values you want to watch automatically, use `Value`:

```python
from pymobile import Value

self.count = Value(0)
Label(self.count).bind()             # re-renders on every change
self.count.set(self.count.get() + 1)
```

---

## 4. A string where a widget belongs

```python
Column("Total: 3")                  # ✗ a str is not a widget
Column(Label("Total: 3"))           # ✓
```

You get:

```
WidgetTypeError: Column children must be widgets, got str ("Total: 3")
  hint: wrap text in Label("…")
```

The same goes for a `None` from a function that forgot to return a widget.

---

## 5. Putting an API key in the app

```python
API_KEY = "sk-live-…"              # ✗ shipped inside the APK, readable by anyone
```

An APK is a ZIP file. `python -m pymobile build` ships `.py` (or `.pyc`, which is
no better) and anyone can unzip it and read every string in it. Anything the
build excludes from the APK — `.env`, `*.jks`, `credentials.json` — is mostly
there to stop you doing this by accident, not to keep a secret.

What works:

- **A token the user created** — `app.save_secret("token", value)` keeps it
  encrypted with a key the Android keystore holds.
- **A secret the app needs to call a service** — put it on a small relay you
  control and have the app call that. There is no client-side hiding.

---

## Where to look next

- `python -m pymobile check` — the same mistakes, found before you run anything.
- `python -m pymobile check-ui` — accessibility gaps and layouts that overflow a
  narrow screen.
- [TUTORIAL.md](TUTORIAL.md) — the fifteen-minute walkthrough these mistakes come
  from.
- `python -m pymobile doctor` — the environment, when a *build* is what fails.
