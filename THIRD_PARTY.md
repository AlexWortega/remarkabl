# Third-party components

- The rM2 framebuffer location and orientation were checked against goMarkableStream (MIT), https://github.com/owulveryck/goMarkableStream at `e07d7aebea2fd2d895673ad650f56194682e9fa9`. `tablet/src/snapshot.rs` independently implements a read-only capture with device, firmware, mapping and pixel-format checks.

- `tablet/src/{qtfb,surface,fb,ink,script}.rs` derive from Riddle, MIT licensed. Source: https://github.com/goodhamgupta/riddle at `f82ec5cb19b0c06815c68ce2e0613e70840fa484`. License: `tablet/LICENSE-Riddle`. Font selection, app lifecycle, queue and server integration are changed for this project.
- Caveat and Noto Sans fonts: Google Fonts, SIL Open Font License. Copies of both licenses are in `assets/` and `tablet/fonts/`.
- XOVI/AppLoad installers are downloaded from their upstream releases and verified using `deploy/upstream-manifest.json`; they retain upstream licenses. AppLoad source: https://github.com/asivery/rm-appload. XOVI source: https://github.com/asivery/rm-xovi-extensions.
- rmfakecloud is AGPL-3.0; the upstream commit is pinned in `deploy/cloud/Dockerfile`. `deploy/cloud/metadata-size.patch` corrects metadata byte sizes after a rename; its regression test runs during the build. Corresponding source: https://github.com/ddvk/rmfakecloud. The startup wrapper belongs to this project.
- Telegram Bot API 10.3 uses the digest-pinned aiogram image in `Dockerfile`, with its Alpine runtime kept in `/opt/telegram`. Image source: https://github.com/aiogram/telegram-bot-api. Bot API source: https://github.com/tdlib/telegram-bot-api, under the Boost Software License. No Telegram client sessions or personal account messages are imported.
