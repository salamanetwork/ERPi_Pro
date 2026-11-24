import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import ts from 'typescript'

const rootDir = new URL('../../', import.meta.url)
const src = new URL('../src/', import.meta.url)
const manifest = JSON.parse(readFileSync(new URL('locales.json', rootDir), 'utf8'))
const registeredCodes = manifest.locales.map(l => l.code)
assert(registeredCodes.includes('en'), 'locales.json must include default en locale')

const readLocaleDict = code => {
  const newPath = new URL(`locales/${code}.json`, src)
  const legacyPath = new URL(`${code}.json`, src)
  const target = existsSync(newPath) ? newPath : legacyPath
  assert(existsSync(target), `Missing translation file for registered locale: ${code}`)
  return JSON.parse(readFileSync(target, 'utf8'))
}

const dicts = Object.fromEntries(registeredCodes.map(code => [code, readLocaleDict(code)]))
const baseKeys = Object.keys(dicts.en).sort()
for (const code of registeredCodes) {
  if (code === 'en') continue
  assert.deepEqual(Object.keys(dicts[code]).sort(), baseKeys, `Translation keys in ${code}.json must match en.json`)
}

const json = name => JSON.parse(readFileSync(new URL(name + '.json', src), 'utf8'))
const keys = new Set([...Object.values(json('errors')), ...Object.values(json('validationMessages'))])
const help = json('help')
for (const route of Object.values(help.topics)) {
  assert.deepEqual(Object.keys(route.ar).sort(), Object.keys(route.en).sort(), 'Help fields must exist in both languages')
}
for (const field of Object.values(help.fields)) assert(field.en && field.ar, 'Field help must be bilingual')

function literals(node) {
  if (ts.isStringLiteralLike(node)) keys.add(node.text)
  else if (ts.isConditionalExpression(node)) { literals(node.whenTrue); literals(node.whenFalse) }
  else if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken) literals(node.right)
}
function script(source) {
  const ast = ts.createSourceFile('messages.ts', source, ts.ScriptTarget.Latest, true)
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 't' && node.arguments.length) {
      literals(node.arguments[0])
    }
    ts.forEachChild(node, visit)
  }
  visit(ast)
}
for (const file of readdirSync(src)) {
  if (!/\.(ts|vue)$/.test(file)) continue
  const source = readFileSync(new URL(file, src), 'utf8')
  if (file.endsWith('.ts')) { script(source); continue }
  const { descriptor } = parse(source)
  if (descriptor.scriptSetup) script(descriptor.scriptSetup.content)
  for (const [, expression] of (descriptor.template?.content ?? '').matchAll(/\{\{([\s\S]*?)\}\}/g)) script(expression)
  for (const [, , key] of source.matchAll(/\bt\(\s*(['"])([^'"\n]+)\1\s*\)/g)) keys.add(key)
}
for (const code of registeredCodes) {
  const missing = [...keys].filter(key => key.trim() && !dicts[code][key]).sort()
  assert.deepEqual(missing, [], `Add ${code} entries for user-facing translation keys`)
}
console.log(`${baseKeys.length} paired translations across [${registeredCodes.join(', ')}], ${Object.keys(help.topics).length} help topics and ${Object.keys(help.fields).length} field explanations checked.`)
