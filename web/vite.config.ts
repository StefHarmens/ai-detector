import tailwindcss from '@tailwindcss/vite';
import devtoolsJson from 'vite-plugin-devtools-json';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

const buildTarget = process.env.AI_DETECTOR_WEB_TARGET?.trim().toLowerCase() || 'node';
// The release, "v0.9.0" for the tag "web/v0.9.0"; set by the release build.
const version =
	process.env.AI_DETECTOR_WEB_VERSION?.trim()
		.replace(/^web\//, '')
		.replaceAll('/', '-') || 'dev';

export default defineConfig({
	define: {
		__AI_DETECTOR_WEB_TARGET__: JSON.stringify(buildTarget),
		__AI_DETECTOR_WEB_VERSION__: JSON.stringify(version)
	},
	server: {
		allowedHosts: ['.local']
	},
	plugins: [tailwindcss(), sveltekit(), devtoolsJson()]
});
