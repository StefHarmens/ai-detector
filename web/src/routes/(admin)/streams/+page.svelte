<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { getStreams } from '$lib/remote/stream.remote';
	import Stream from './stream.svelte';
	import { Plus } from '@lucide/svelte';
	import { SvelteSet } from 'svelte/reactivity';

	const ACTIVE_STREAM_LIMIT = 4;
	const streams = await getStreams();

	let visibleSources = new SvelteSet<string>();

	const activeSources = $derived(
		streams
			.filter((stream) => visibleSources.has(stream.source))
			.slice(0, ACTIVE_STREAM_LIMIT)
			.map((stream) => stream.source)
	);

	function setStreamVisibility(source: string, visible: boolean) {
		if (visibleSources.has(source) === visible) {
			return;
		}

		if (visible) {
			visibleSources.add(source);
		} else {
			visibleSources.delete(source);
		}
	}

	function trackStreamVisibility(node: HTMLElement, source: string) {
		const observer = new IntersectionObserver(
			([entry]) => {
				setStreamVisibility(source, entry.isIntersecting);
			},
			{ threshold: 0.25 }
		);
		observer.observe(node);

		return {
			destroy() {
				observer.disconnect();
				setStreamVisibility(source, false);
			}
		};
	}
</script>

<section class="space-y-6">
	<header class="space-y-1">
		<div class="flex items-center justify-between">
			<h1 class="text-2xl font-semibold tracking-tight">Live streams</h1>
			<Button href="/streams/add" variant="outline"><Plus /> Add stream</Button>
		</div>
		<p class="text-sm text-muted-foreground">Live stream from your RTSP sources.</p>
	</header>

	<div class="grid gap-2 lg:grid-cols-2">
		{#each streams as stream (stream.source)}
			<div use:trackStreamVisibility={stream.source}>
				{#if activeSources.includes(stream.source)}
					<Stream label={stream.label} source={stream.source} />
				{:else}
					<div class="relative aspect-video w-full bg-black">
						<div
							class="absolute inset-0 flex items-center justify-center px-4 text-center text-xs text-white/60"
						>
							Preview paused
						</div>
					</div>
				{/if}
			</div>
		{/each}
	</div>
</section>
