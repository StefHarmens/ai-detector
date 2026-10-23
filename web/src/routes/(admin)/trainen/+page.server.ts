import { trainingInfo } from '$lib/server/training';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async () => ({ training: await trainingInfo() });
