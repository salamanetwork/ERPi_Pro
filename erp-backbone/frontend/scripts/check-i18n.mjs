import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import ts from 'typescript'

const src = new URL('../src/', import.meta.url)
const json = name => JSON.parse(readFileSync(new URL(name + '.json', src), 'utf8'))
const ar = json('ar')
const en = json('en')
assert.deepEqual(Object.keys(ar).sort(), Object.keys(en).sort(), 'Arabic and English translation keys must match')
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
  // Include quoted translation calls used by attributes and directives.
  for (const [, , key] of source.matchAll(/\bt\(\s*(['"])([^'"\n]+)\1\s*\)/g)) keys.add(key)
}
const missing = [...keys].filter(key => key.trim() && !ar[key]).sort()
assert.deepEqual(missing, [], 'Add Arabic and English entries for user-facing translation keys')
console.log(`${Object.keys(ar).length} paired translations, ${Object.keys(help.topics).length} help topics and ${Object.keys(help.fields).length} field explanations checked.`)
