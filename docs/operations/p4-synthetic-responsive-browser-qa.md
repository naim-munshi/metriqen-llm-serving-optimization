# P4 synthetic real-browser responsive/keyboard regression test

The `p4-synthetic-chromium-responsive` GitHub CI job renders the **actual
checked-in Go Operations HTML/CSS/JS** with headless Chromium at widths
**320, 390, 768 and 1440 CSS pixels**.

It checks:
- no unexpected whole-page horizontal overflow or offscreen health cards;
  the small-screen navigation may intentionally scroll *inside its rail*;
- the keyboard's first Tab is a visible-on-focus skip link, activating it
  focuses the main landmark, and keyboard navigation can reach the last
  mobile section link;
- automatic refresh Pause/Resume is keyboard-operable with correct ARIA
  pressed state and manual Refresh remains available;
- 44px-or-taller Pause/Refresh buttons and a rendered, nonempty dashboard;
- the embedded local browser JS starts and reads only **synthetic**
  intercepted fixed `/health` and `/metrics` responses, with **no**
  real Go gateway, Ollama, FastAPI, model, account, or network URL.
- no real customer data, prompts, session secrets, screenshot or
  private model response is collected or published.

Dependencies are pinned for the GitHub runner; a local invocation is
optional and is not required for Mac inference:

```sh
npm install --no-save --no-package-lock --prefix tests/ui/headless playwright@1.56.1
tests/ui/headless/node_modules/.bin/playwright install chromium
node tests/ui/headless/responsive_browser_check.cjs
```

The test is deliberately conservative: a failure identifies a concrete
Chromium synthetic rendering/keyboard regression before human review.
A green CI result **does not mean** that actual Safari 320/390px,
VoiceOver/touch scrolling, OS appearance, authentication, loaded model
state, approved public demo screenshot or the broader P4/P6 acceptance
was tested. The owner's genuine Mac Safari/browser journey must still
be reviewed according to
[Portfolio final Mac handoff](portfolio-final-mac-handoff.md).
