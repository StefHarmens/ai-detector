<script lang="ts">
	import NavUser from './nav-user.svelte';
	import * as Sidebar from '$lib/components/ui/sidebar/index.js';
	import { resolve } from '$app/paths';
	import logo from '$lib/assets/logo.svg';
	import type { ComponentProps } from 'svelte';
	import NavMain from './nav-main.svelte';
	import VersionInfo from './version-info.svelte';
	import type { NavMenu, NavItem } from './types';

	let {
		title,
		subtitle,
		user,
		menu,
		secondaryMenu,
		ref = $bindable(null),
		...restProps
	}: {
		title: string;
		subtitle: string;
		menu: NavMenu[];
		secondaryMenu?: NavMenu[];
		user?: {
			name?: string;
			email?: string;
			avatar?: string;
			items: NavItem[];
			logout: () => void;
		};
	} & ComponentProps<typeof Sidebar.Root> = $props();
</script>

<Sidebar.Root bind:ref variant="inset" {...restProps}>
	<Sidebar.Header>
		<Sidebar.Menu>
			<Sidebar.MenuItem>
				<Sidebar.MenuButton size="lg" class="h-auto py-2">
					{#snippet child({ props })}
						<a href={resolve('/')} {...props}>
							<!-- The logo is green on white, so it stays readable in dark mode. -->
							<div
								class="flex size-14 shrink-0 items-center justify-center rounded-lg bg-white p-1"
							>
								<img src={logo} alt="" class="size-full object-contain" />
							</div>
							<div class="grid flex-1 gap-0.5 text-start text-sm leading-tight">
								<span class="truncate text-base font-semibold">{title}</span>
								<span class="text-xs leading-snug">{subtitle}</span>
							</div>
						</a>
					{/snippet}
				</Sidebar.MenuButton>
			</Sidebar.MenuItem>
		</Sidebar.Menu>
	</Sidebar.Header>
	<Sidebar.Content>
		{#each menu as item (item.title)}
			<NavMain title={item.title} items={item.items} />
		{/each}
		{#each secondaryMenu || [] as item (item.title)}
			<NavMain title={item.title} items={item.items} size="sm" class="mt-auto" />
		{/each}
	</Sidebar.Content>
	<Sidebar.Footer>
		<VersionInfo />
		{#if user}
			<NavUser {user} items={user.items} logout={user.logout} />
		{/if}
	</Sidebar.Footer>
</Sidebar.Root>
