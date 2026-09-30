<script lang="ts">
	import './layout.css';
	import logo from '$lib/assets/logo.svg';
	import { Toaster } from '$lib/components/ui/sonner';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={logo} />
</svelte:head>

{@render children()}
<Toaster />
