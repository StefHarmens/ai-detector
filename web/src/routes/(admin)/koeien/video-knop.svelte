<script lang="ts">
	import { Button, type ButtonSize } from '$lib/components/ui/button';
	import * as Dialog from '$lib/components/ui/dialog';
	import PlayIcon from '@lucide/svelte/icons/play';
	import { sightingPhoto, sightingVideo } from './api';

	type Props = {
		id: string;
		title: string;
		size?: ButtonSize;
	};

	let { id, title, size = 'sm' }: Props = $props();
	let open = $state(false);
</script>

<Button type="button" {size} variant="secondary" onclick={() => (open = true)}>
	<PlayIcon /> Video
</Button>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-4xl">
		<Dialog.Header>
			<Dialog.Title>{title}</Dialog.Title>
		</Dialog.Header>
		{#if open}
			<!-- svelte-ignore a11y_media_has_caption -->
			<video
				class="w-full rounded-md bg-black"
				controls
				autoplay
				playsinline
				poster={sightingPhoto(id, 'controle')}
				src={sightingVideo(id)}
			></video>
		{/if}
	</Dialog.Content>
</Dialog.Root>
