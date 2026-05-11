# Meta/Instagram temporary hosting strategy

Meta Graph API container creation for Instagram video publishing requires a publicly accessible media URL. CentralPublishingHub owns that requirement for EconomikaNoticias.

## Contract

EconomikaNoticias should send one of these fields:

- `video_path` when CentralPublishingHub runs locally or in an environment that can access the rendered file.
- `video_url` when the media is already hosted publicly.

CentralPublishingHub decides how to resolve the final public URL:

- If `video_url` is provided, it is used directly.
- If an Instagram target is requested and only `video_path` is provided, CentralPublishingHub uploads the local file to temporary public hosting.
- If no public URL can be resolved, CentralPublishingHub returns a structured error and does not call Meta/Instagram upload APIs.

Temporary hosting is not an EconomikaNoticias workaround. Gofile, Uguu, and Catbox are Hub-level media hosting adapters used to satisfy Meta Graph API requirements.

## Temporary hosting order

The fallback order is:

1. Gofile
2. Uguu
3. Catbox

Tests must mock these adapters. Unit tests must not upload files or call external services.

## Platform scope

Instagram targets that require a public URL:

- `instagram_reel`
- `instagram_story`
- `instagram_feed`
- `instagram_post`

Supported aliases:

- `instagram` -> `instagram_reel`
- `reel` -> `instagram_reel`
- `story` -> `instagram_story`
- `feed` -> `instagram_feed`
- `post` -> `instagram_feed`
- `instagram_post` -> `instagram_feed`

YouTube Shorts should use local `video_path` and must not trigger temporary hosting.

Facebook remains legacy/out of scope for this contract.

## Feed/Post status

Instagram Feed/Post is a planned target in the media URL resolver and alias layer. Full Graph API publishing for Feed/Post is not guaranteed for MVP unless proven by tests or manual validation. Until implemented, CentralPublishingHub should return `NOT_IMPLEMENTED` cleanly.

Immediate publishing for Instagram Reels and Stories is the required MVP behavior.
