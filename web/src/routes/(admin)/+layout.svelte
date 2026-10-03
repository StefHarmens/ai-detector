<script lang="ts">
	import AppSidebar from '$lib/components/app-sidebar.svelte';
	import * as Breadcrumb from '$lib/components/ui/breadcrumb/index.js';
	import { Separator } from '$lib/components/ui/separator/index.js';
	import * as Sidebar from '$lib/components/ui/sidebar/index.js';
	import { resolve } from '$app/paths';
	import logo from '$lib/assets/logo.svg';
	import TVIcon from '@lucide/svelte/icons/tv';
	import CameraIcon from '@lucide/svelte/icons/camera';
	import WrenchIcon from '@lucide/svelte/icons/wrench';
	import BellIcon from '@lucide/svelte/icons/bell';
	import { page } from '$app/state';
	import GithubIcon from '@lucide/svelte/icons/github';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import MilkIcon from '@lucide/svelte/icons/milk';
	import CircleHelpIcon from '@lucide/svelte/icons/circle-question-mark';
	import ScrollTextIcon from '@lucide/svelte/icons/scroll-text';

	let { children } = $props();

	const menu = [
		{
			title: 'Overview',
			items: [
				{
					title: 'Koeien',
					url: '/koeien',
					icon: MilkIcon
				},
				{
					title: 'Twijfel',
					url: '/twijfel',
					icon: CircleHelpIcon
				},
				{
					title: 'Detections',
					url: '/detections',
					icon: CameraIcon
				},
				{
					title: 'Streams',
					url: '/streams',
					icon: TVIcon
				},
				{
					title: 'Logboek',
					url: '/logboek',
					icon: ScrollTextIcon
				}
			]
		},
		{
			title: 'Settings',
			items: [
				{
					title: 'Setup',
					url: '/setup',
					icon: CircleCheckIcon
				},
				{
					title: 'Notifications',
					url: '/notifications',
					icon: BellIcon
				},
				{
					title: 'Detectors',
					url: '/detectors',
					icon: WrenchIcon
				}
			]
		}
	];

	const secondaryMenu = [
		{
			title: 'Support',
			items: [
				{
					title: 'Github',
					url: 'https://github.com/StefHarmens/ai-detector',
					icon: GithubIcon
				}
			]
		}
	];

	// const user = {
	// 	name: 'User',
	// 	email: 'AI Detector',
	// 	items: [
	// 		{
	// 			title: 'Account',
	// 			url: '/account',
	// 			icon: BadgeCheckIcon
	// 		}
	// 	],
	// 	logout: () => console.log('logout')
	// };
</script>

<Sidebar.Provider>
	<AppSidebar title="CowCatcher" subtitle="voor melkveehouderij Hoentjen" {menu} {secondaryMenu} />
	<Sidebar.Inset>
		<header class="flex h-16 shrink-0 items-center gap-2">
			<div class="flex items-center gap-2 px-4">
				<Sidebar.Trigger class="-ms-1" />
				<!-- On a phone the sidebar is hidden, so the logo shows here. -->
				<a href={resolve('/')} class="md:hidden" aria-label="CowCatcher">
					<img src={logo} alt="" class="size-8 rounded bg-white p-0.5" />
				</a>
				<Separator orientation="vertical" class="me-2 data-[orientation=vertical]:h-4" />
				<Breadcrumb.Root>
					<Breadcrumb.List>
						<Breadcrumb.Item class="hidden md:block">
							<Breadcrumb.Link href="/">CowCatcher</Breadcrumb.Link>
						</Breadcrumb.Item>
						{#each page.url.pathname.split('/').filter(Boolean) as path, index (`${index}:${path}`)}
							<Breadcrumb.Separator class="hidden md:block" />
							<Breadcrumb.Item>
								<Breadcrumb.Page>{path.charAt(0).toUpperCase() + path.slice(1)}</Breadcrumb.Page>
							</Breadcrumb.Item>
						{/each}
					</Breadcrumb.List>
				</Breadcrumb.Root>
			</div>
		</header>
		<div class="flex flex-1 flex-col gap-4 p-4 pt-0">
			{@render children()}
		</div>
	</Sidebar.Inset>
</Sidebar.Provider>
