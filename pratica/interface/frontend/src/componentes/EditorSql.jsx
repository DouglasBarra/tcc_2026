import { useEffect, useRef } from 'react';
import { basicSetup } from 'codemirror';
import { EditorState, RangeSetBuilder, StateEffect, StateField } from '@codemirror/state';
import { Decoration, EditorView } from '@codemirror/view';
import { sql, MSSQL } from '@codemirror/lang-sql';
import { HighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';

// Tema e realce usam variaveis CSS (tokens.css): acompanham o tema claro/escuro sem recriar o editor.
const tema = EditorView.theme({
  '&': { backgroundColor: 'var(--bg-2)', color: 'var(--sql-ident)', height: '100%', fontSize: '15.5px' },
  '.cm-scroller': { fontFamily: 'var(--fonte-mono)', lineHeight: '1.55' },
  '.cm-content': { caretColor: 'var(--acento)' },
  '.cm-gutters': { backgroundColor: 'var(--bg-2)', color: 'var(--texto-2)', borderRight: '1px solid var(--borda)' },
  '.cm-activeLine': { backgroundColor: 'color-mix(in srgb, var(--acento) 8%, transparent)' },
  '.cm-activeLineGutter': { backgroundColor: 'color-mix(in srgb, var(--acento) 8%, transparent)', color: 'var(--texto)' },
  '&.cm-focused': { outline: '2px solid var(--foco)', outlineOffset: '-2px' },
  '.cm-selectionBackground, &.cm-focused .cm-selectionBackground': { backgroundColor: 'color-mix(in srgb, var(--acento) 28%, transparent)' },
  '.cm-cursor': { borderLeftColor: 'var(--acento)' },
  '.cm-tooltip': { backgroundColor: 'var(--bg-3)', color: 'var(--texto)', border: '1px solid var(--borda-forte)' },
});

const realce = HighlightStyle.define([
  { tag: [t.keyword, t.operatorKeyword, t.modifier], color: 'var(--sql-keyword)', fontWeight: '600' },
  { tag: [t.string, t.special(t.string)], color: 'var(--sql-string)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--sql-number)' },
  { tag: [t.lineComment, t.blockComment], color: 'var(--sql-comment)', fontStyle: 'italic' },
  { tag: [t.typeName, t.standard(t.name)], color: 'var(--sql-type)' },
  { tag: [t.name, t.propertyName], color: 'var(--sql-ident)' },
]);

// Marcas de evidencia: linhas do SQL citadas por achados do diagnostico (tcclab.diagnostico). So existem quando o
// texto do editor e exatamente o texto que foi analisado (quem usa o componente decide, ver Consulta.jsx).
const definirMarcas = StateEffect.define();
const campoMarcas = StateField.define({
  create: () => Decoration.none,
  update(deco, tr) {
    let novo = deco.map(tr.changes);
    for (const e of tr.effects) {
      if (!e.is(definirMarcas)) continue;
      const b = new RangeSetBuilder();
      const doc = tr.state.doc;
      const porLinha = new Map();
      for (const m of e.value) if (m.linha >= 1 && m.linha <= doc.lines) porLinha.set(m.linha, [...(porLinha.get(m.linha) ?? []), m.titulo]);
      for (const linha of [...porLinha.keys()].sort((a, c) => a - c)) {
        b.add(doc.line(linha).from, doc.line(linha).from, Decoration.line({ class: 'cm-evidencia', attributes: { title: porLinha.get(linha).join(' | ') } }));
      }
      novo = b.finish();
    }
    return novo;
  },
  provide: (f) => EditorView.decorations.from(f),
});

/** Editor T-SQL (CodeMirror 6, dialeto MSSQL). `valor` controlado por fora; `somenteLeitura` desliga a edicao. */
export function EditorSql({ valor, onChange, somenteLeitura = false, rotulo = 'Editor de consulta T-SQL', marcas = [] }) {
  const host = useRef(null);
  const view = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    const estado = EditorState.create({
      doc: valor ?? '',
      extensions: [
        basicSetup,
        sql({ dialect: MSSQL, upperCaseKeywords: false }),
        tema,
        syntaxHighlighting(realce),
        EditorView.lineWrapping,
        campoMarcas,
        EditorState.readOnly.of(somenteLeitura),
        EditorView.editable.of(!somenteLeitura),
        EditorView.contentAttributes.of({ 'aria-label': rotulo, 'aria-multiline': 'true' }),
        EditorView.updateListener.of((u) => {
          if (u.docChanged) onChangeRef.current?.(u.state.doc.toString());
        }),
      ],
    });
    view.current = new EditorView({ state: estado, parent: host.current });
    return () => {
      view.current?.destroy();
      view.current = null;
    };
    // o editor e recriado so quando muda o modo (leitura/edicao); o texto e sincronizado no efeito abaixo
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [somenteLeitura, rotulo]);

  useEffect(() => {
    const v = view.current;
    if (v && (valor ?? '') !== v.state.doc.toString()) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: valor ?? '' } });
    }
  }, [valor]);

  useEffect(() => {
    view.current?.dispatch({ effects: definirMarcas.of(marcas) });
  }, [marcas, somenteLeitura, rotulo]);

  return <div className="editor-corpo" ref={host} data-testid="editor-sql" />;
}
