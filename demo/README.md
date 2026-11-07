# The recorded demo and the screenshots

`make demo-media` records the product video and takes the README screenshots with a real headless Chromium against the running stack. The committed copies live in [`docs/media`](../docs/media): `demo.mp4` (the full walk-through), `demo.gif` (the clip at the top of the README) and `screenshots/`.

## What the video shows

The screen is split. On the left a customer uses the demo shop and its chat widget; on the right the shop's admin works in the panel. English captions on top say what is happening, chapter cards separate the parts, and a fake cursor marks every click, so the video needs no voice-over.

| # | Chapter | What happens |
| --- | --- | --- |
| 0 | Introduction | Title card, what to expect, the admin signs in |
| 1 | Knowledge base | The admin uploads a document and watches it become `ready`; a visitor who is not signed in asks about it and gets a cited answer |
| 2 | Any language | A Turkish question is answered from the shop's Turkish documents, with sources |
| 3 | Real work through the shop's API | A signed-in customer asks about an order; the admin opens the conversation |
| 4 | A human decides risky actions | A refund request pauses; the admin reads the arguments and approves; the answer reaches the customer without a reload and the shop's order list shows the refund |
| 5 | Memory | A customer states a preference, starts a new chat and is reminded of it; the admin reads, and could erase, what is remembered |
| 6 | Security | A request to change someone else's order is stopped by a policy in code, a prompt injection is stopped before any model reads it, a poisoned document is flagged on arrival, and the Security page lists every event |
| 7 | Control and visibility | The tools with their risk levels and argument policies, and the usage and cost per day |

## Recording it again

```bash
make demo            # the stack must be running, ideally freshly seeded
make demo-media      # restarts the mock shop, records, takes screenshots, converts, copies to docs/media
```

Use a stack that nothing else has used: the numbers on the Overview and Usage pages are those of the recording, the shop refunds an order once, and the firewall allows five changes per customer and hour (the target restarts the mock shop first). The model's answers differ a little from take to take, so run it again if a take is not good. The admin password is `DEMO_ADMIN_PASSWORD` (default `demo-admin-password`).

The scripts: `record.py` (a wrapper page holds the two sites in frames, the captions and the cursor; a small `Director` types, glides the cursor and waits for replies; `ONLY=4,5` records chosen chapters while developing), `screenshots.py` (1440x900 at a device scale factor of 1.5, light and dark) and `make_media.sh` (runs both inside the Playwright container and converts with ffmpeg).
