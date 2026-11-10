import assert from 'node:assert/strict';
import { readFile, access } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { Context } from '@deepseek-ai/cordis';
import SkillRegistry, { renderSkillContent } from '@deepseek-ai/dsh-skill';
import * as plugin from '../index.js';

test('DSH discovers, loads and unloads the packaged skill with usable resources', async () => {
  const ctx = new Context();
  const registry = ctx.plugin(SkillRegistry);
  await registry.await();
  const fork = ctx.plugin(plugin);
  try {
    await fork.await();
    const catalog = await ctx.skills.list();
    assert.deepEqual(catalog.map(item => item.name), ['wanlu-strava-sync']);
    const skill = await ctx.skills.get('wanlu-strava-sync');
    assert.equal(skill.invocation.userInvocable, true);
    assert.equal(skill.invocation.modelInvocable, true);
    assert.equal(skill.source, 'bundled');
    const root = fileURLToPath(new URL('../skills/wanlu-strava-sync/', import.meta.url));
    assert.equal(skill.resourceBase.path, root.replace(/\/$/, ''));
    const source = await readFile(join(root, 'SKILL.md'), 'utf8');
    const body = source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').trim();
    assert.equal(skill.content.trim(), body);
    assert.ok(renderSkillContent(skill).includes(skill.resourceBase.path));
    for (const resource of ['references/onelap.md', 'references/strava-browser.md', 'references/coordinates.md', 'scripts/wanlu.py', 'scripts/fit_coordinates.py']) {
      await access(join(skill.resourceBase.path, resource));
    }
    await fork.dispose();
    assert.deepEqual(await ctx.skills.list(), []);
    assert.equal(await ctx.skills.get('wanlu-strava-sync'), undefined);
  } finally {
    await fork.dispose();
    await registry.dispose();
  }
});
