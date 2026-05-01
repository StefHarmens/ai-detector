# AI Detector — Web

The frontend for the AI Detector project. Built with [SvelteKit](https://kit.svelte.dev/).

> **Status: In development.** Not yet ready for production use.

## What it does

- Displays a live view of active detection streams.
- Shows a history of past detections with images and video clips.

## Development

```bash
npm install
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

## Building

```bash
npm run build
npm run preview
```

The default build targets the Windows executable package. To build the Docker/Node server variant locally, use `AI_DETECTOR_WEB_TARGET=docker pnpm build`. That same target also controls whether the server reads `config.json`, `app.json`, and `detections/` from the working directory or next to the packaged executable.

When the executable starts, it serves port 80 by default, prints the local browser URL plus LAN URLs for the computer's IPv4 network addresses, starts an mDNS responder for the computer's `.local` name, and opens the `.local` URL in the default browser when available. For example, it opens `http://my-pc.local` and also prints `http://192.168.1.23` as a fallback. Set `PORT` to override the default or `OPEN_BROWSER=false` to skip opening the browser. The `.local` address requires UDP multicast on the local network and may still be blocked by Windows Firewall or Wi-Fi client isolation.

## Docker

From the repository root:

```bash
docker compose up -d aidetector web
```

The container serves the app on [http://localhost](http://localhost). In Compose, the `web` service reuses `aidetector`'s mounted `config.json`, `app.json`, input files, and `detections/` via `volumes_from`.
