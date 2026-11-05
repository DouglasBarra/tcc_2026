import { descreverOrigem } from '../lib/origem.js';
import { ehNumero, fmtInt, VAZIO } from '../lib/formatar.js';

/**
 * Numero na tela, SEMPRE com origem rastreavel (tooltip no hover/foco e aria-label).
 * Sem dado (null, undefined, NaN): mostra travessao e nunca "0".
 * `focavel={false}` dentro de controles interativos (botao/link): a origem continua no hover e no nome acessivel.
 */
export function Num({ valor, formato = fmtInt, origem, rotulo, focavel = true }) {
  if (!ehNumero(valor)) {
    return (
      <span className="num vazio-num" aria-label={`${rotulo ? `${rotulo}: ` : ''}sem dado`}>
        {VAZIO}
      </span>
    );
  }
  const texto = formato(valor);
  const o = typeof origem === 'string' ? origem : descreverOrigem(origem);
  if (!o && import.meta.env?.DEV) console.error('Num sem origem rastreavel:', rotulo ?? texto);
  const rastro = o || 'origem não informada';
  return (
    <span className="num" tabIndex={focavel ? 0 : undefined} data-origem={rastro} aria-label={`${rotulo ? `${rotulo}: ` : ''}${texto}. Origem: ${rastro}`}>
      {texto}
    </span>
  );
}
