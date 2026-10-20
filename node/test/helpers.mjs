import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
export const FIXTURES = join(ROOT, 'fixtures')
export const SCENARIOS = readdirSync(join(FIXTURES, 'scenarios'))
  .filter((f) => f.endsWith('.json'))
  .sort()
  .map((f) => join(FIXTURES, 'scenarios', f))
export const ROUTES = JSON.parse(readFileSync(join(ROOT, 'routes.example.json'), 'utf8')).routes
export const SECRET = 'whsec_examples_test'

export const BRICKELL = 'a1b2c3d4-0002-4000-8000-000000000001'

export const fixture = (name) => JSON.parse(readFileSync(join(FIXTURES, name), 'utf8'))
