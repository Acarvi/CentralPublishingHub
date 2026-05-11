# Central Publishing Hub 🚀

Unified microservice for cross-platform social media publishing.

## Overview
The **Central Publishing Hub** is a FastAPI-based service designed to centralize and automate publishing across Instagram, Facebook, and YouTube. It uses a multi-account architecture to manage credentials for various brands (e.g., Economika).

## Features
- **Multi-Brand Support**: Manage various tokens and IDs via `data/accounts_db.json`.
- **Cross-Platform**: Support for Instagram Reels/Stories, Facebook Reels/Stories, and YouTube Shorts.
- **Scheduling**: Built-in queue management for deferred publishing.
- **Location Search**: Integrated Instagram location tagging.
- **Temporary Hosting**: Automatic media hosting via Gofile/Uguu/Catbox for Meta API compatibility.

## Meta/Instagram Media Hosting
For Instagram publishing, CentralPublishingHub owns public URL resolution. Callers such as EconomikaNoticias should send `video_path` when the Hub can access the local file, or `video_url` when media is already hosted. The Hub uses temporary hosting adapters in this order: Gofile, Uguu, Catbox.

See [docs/meta-instagram-hosting.md](docs/meta-instagram-hosting.md) for the formal contract, aliases, and Feed/Post status.

## API Endpoints
- `POST /api/v1/publish`: Immediate publishing.
- `POST /api/v1/schedule`: Add posts to the queue.
- `GET /api/v1/queue`: View pending tasks.
- `GET /api/v1/locations`: Search for IG/FB location IDs.

## Setup
1. Copy `.env.example` to `.env` and fill in credentials.
2. Install dependencies: `pip install -r requirements.txt`.
3. Run service (OBLIGATORIO para EconomikaNoticias):
   ```bash
   python main.py
   ```
   (El servidor arrancará en el puerto 8000).

## API Endpoints
...
This project follows the **Skills Architecture** protocol (.agent/skills/).
- [Tests](.agent/skills/skill_testing.md): Pytest + Coverage.
- [Documentation](.agent/skills/skill_documentation.md): Maintenance rules.
- [Navigation](.agent/skills/skill_comet_navigation.md): Comet Browser integration.
