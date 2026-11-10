import { fileURLToPath } from 'node:url';
import { apply as registerFilesystemSkills } from '@deepseek-ai/dsh-skill-filesystem';

export const name = 'wanlu-strava-sync';
export const inject = ['skills'];

export function apply(ctx) {
  registerFilesystemSkills(ctx, {
    providerName: 'wanlu-strava-sync',
    includeDefaultRoots: false,
    bundledSkillDir: fileURLToPath(new URL('./skills/', import.meta.url)),
    watch: false,
  });
}
