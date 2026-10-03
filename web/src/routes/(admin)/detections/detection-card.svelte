<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import CardOverlay from '$lib/components/card-overlay.svelte';
	import type { Metadata } from '$lib/schema';

	const stageLabels = {
		approved: 'Approved',
		rejected: 'Rejected',
		unvalidated: 'Unvalidated'
	} as const;

	const stageBadgeVariants = {
		approved: 'default',
		rejected: 'destructive',
		unvalidated: 'secondary'
	} as const;

	const stageBadgeClasses = {
		approved: 'bg-emerald-600 text-white',
		rejected: '',
		unvalidated: 'bg-amber-500 text-black'
	} as const;

	const overlayBadgeClass = 'bg-black/50 text-white';

	type Props = {
		entry: Metadata;
	};

	let { entry }: Props = $props();
	let isPlaying = $state(false);

	const stage = $derived(
		entry.validated === true ? 'approved' : entry.validated === false ? 'rejected' : 'unvalidated'
	);

	function getResource(resource: string) {
		return `/detections/${[entry.type, stage, entry.timestamp, resource]
			.map((segment) => encodeURIComponent(segment))
			.join('/')}`;
	}

	function capitalize(value: string) {
		return value.charAt(0).toUpperCase() + value.slice(1);
	}

	function formatTime(value: string) {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) {
			return value;
		}
		return new Intl.DateTimeFormat(undefined, { timeStyle: 'medium' }).format(date);
	}
</script>

<CardOverlay hide={isPlaying}>
	<video
		class="block h-auto w-full bg-black"
		controls
		preload="none"
		poster={getResource('best.jpg')}
		onplay={() => (isPlaying = true)}
		onpause={() => (isPlaying = false)}
		onended={() => (isPlaying = false)}
	>
		<source src={getResource('video.mp4')} type="video/mp4" />
		Your browser cannot play this video.
	</video>
	{#if entry.hires && !isPlaying}
		<div class="absolute end-2 bottom-14 z-20 flex gap-1">
			{#each [['hires-best.jpg', '4K beeld'], ['hires.jpg', '4K uitsnede']] as [file, title] (file)}
				<a
					href={getResource(file)}
					target="_blank"
					rel="noreferrer"
					class="rounded-md bg-black/60 px-2 py-1 text-xs font-semibold text-white hover:bg-black/80"
					title="Openen op volle grootte, om nummers te lezen"
				>
					{title}
				</a>
			{/each}
		</div>
	{/if}

	{#snippet overlay()}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			<Badge variant={stageBadgeVariants[stage]} class={stageBadgeClasses[stage]}>
				{stageLabels[stage]}
			</Badge>
			<Badge variant="secondary" class={overlayBadgeClass}>
				{capitalize(entry.type)}
			</Badge>
			<Badge variant="secondary" class={overlayBadgeClass}>Time: {formatTime(entry.start)}</Badge>
			<Badge variant="secondary" class={overlayBadgeClass}
				>Duration: {entry.duration.toFixed(2)}s</Badge
			>
			<Badge variant="secondary" class={overlayBadgeClass}>
				Confidence: {(entry.confidence * 100).toFixed(1)}%
			</Badge>
		</div>
	{/snippet}
</CardOverlay>
