# Estado real e fila do produto

Este arquivo separa o que já funciona do que existe apenas como fundação. Ele
evita que uma tabela criada no banco seja confundida com uma integração pronta
na interface e nos conectores.

## Matriz atual

| Área | Estado | Limite atual |
| --- | --- | --- |
| Jobs nomeados de playlists | Implementado | Selecionam **contas** (`account_id`); duas contas do mesmo serviço podem participar. |
| Pareamento explícito de playlists | Implementado | Membros por `account_id`; jobs legados migrados para `:default` sem quebra. |
| Transferência entre serviços | Implementado | Playlists; conta ao vivo x snapshot canônico são classificados por `auth_mode` e nunca se confundem. |
| Versões de playlists | Implementado | Histórico limitado por playlist/provedor. |
| Musify | Implementado/parcial | Importa `user.hive`, superfícies e recap; exporta playlists por link. A atualização continua manual. |
| Sonora | Implementado/parcial | Backup-v2 e bridge LAN pareada; depende da compatibilidade do protocolo do app. |
| Recap unificado | Implementado | Filtro por combinação de contas em card anual, histórico mensal, tops, minutos e breakdown. |
| Banco canônico | Implementado/parcial | Musify, Sonora, exports oficiais, listens e importação **ao vivo** (botão por conta) o alimentam; curtidas/álbuns/artistas ao vivo seguem sem adapters comerciais. |
| Curtidas, álbuns e artistas | Parcial | Modelo canônico e adapters Musify/Sonora existem; nos serviços comerciais só via export oficial (marcado `r`/`-` por superfície). |
| Múltiplas contas no mesmo serviço | Implementado | Perfis ao vivo com credenciais/tokens/cookies/caches isolados por conta; adicionar, renomear, pausar, remover e importar pela UI. |
| Spotify totalmente sem OAuth | Implementado/parcial | `sp_dc` cobre rootlist/pastas, leitura, busca e escrita, com arquivo 0600 **por conta**. Curtidas/álbuns/artistas contínuos ainda dependem de export oficial. |
| Backup do próprio SYNC | Implementado | ZIP versionado e validado; `sp_dc` (default e por conta) é excluído até existir criptografia. |

## Concluído na rodada multi-conta

- ~~Jobs nomeados selecionam provedores~~ → selecionam contas (`account_id`), com
  origem e destinos por conta e duas contas do mesmo serviço no mesmo job.
- ~~Accounts trabalha com uma conexão por provedor~~ → perfis nomeados com
  adicionar, conectar/reconectar, renomear, pausar, superfícies por conta e
  remoção com confirmação; secrets nunca são ecoados à UI.
- ~~Conectores operam sobre o default~~ → cada connector é instanciado por conta:
  Spotify `sp_dc`/OAuth, YouTube Music browser/OAuth e os demais leem e gravam
  somente o namespace da conta selecionada.
- ~~Snapshots x contas ao vivo se confundiam no browse~~ → classificação única
  por `auth_mode` (`canonical_target.is_canonical_account`) usada por
  PlaylistService, TransferService e Accounts.
- ~~N-way colidia no `source`~~ → reconcile chaveia por `state_key`; duas contas
  Spotify mantêm diretórios, caches e baselines separados.
- ~~Filtro de recap por conta~~ → implementado (combinação de contas em todos os
  totais e breakdown).
- ~~Backup geral não refletia registry~~ → backup/restore inclui `ACCOUNTS`,
  jobs com `accounts` e dados canônicos por conta; backups por conta seguem sem
  secrets.
- ~~E2E podia ficar preso na CI~~ → watchdog global no runner (30 min) com
  teardown forçado e falha explícita.

## Próximos blocos

### 1. Superfícies comerciais contínuas

- Importar curtidas, álbuns e artistas ao vivo (hoje: export oficial ou import
  Musify/Sonora; os conectores comerciais marcam essas superfícies como `-` ou
  `r` conforme o adapter real).
- Sincronizar superfícies entre serviços somente quando ambos os lados
  declararem suporte seguro (`rw`).

### 2. Spotify Web sem Premium — superfícies contínuas

- ~~Transformar o modo `sp_dc` em backend completo, não somente de escrita.~~
- ~~Listar a `rootlist`, pastas, playlists próprias e playlists salvas.~~
- Importar curtidas, álbuns e artistas seguidos via Web Player (hoje só export
  oficial; a superfície é marcada conforme a capacidade real).
- ~~Pesquisar/resolver faixas sem depender do cliente OAuth.~~
- ~~Renovar o token temporário, detectar cookie expirado e mostrar o erro real.~~
- ~~Expor os estados `OAuth oficial`, `Web/cookie` e `Desativado`.~~

Limitação conhecida (uso real):

- No modo cookie, o browse/import não traz **imagens de capa** das playlists —
  o endpoint do web player não expõe a arte (aparece vazio no painel; o import
  funciona normalmente). Caminho futuro: buscar a capa por nome via pesquisa,
  ou usar o client OAuth oficial apenas para a arte quando disponível.

### 3. Importação/sincronização automática entre serviços

- Hoje só **playlists** sincronizam sozinhas (jobs nomeados por conta,
  one-way/N-way) e o import ao vivo por conta traz playlists para o banco
  canônico. Não existe ainda um "importar tudo" de um serviço para outro.
- Importar **a biblioteca inteira** (playlists + curtidas + álbuns + artistas)
  de uma conta conectada para outra, com mapeamento conta-a-conta, dry-run e
  limites de segurança — depende dos adapters de superfície (bloco 1).
- Sincronização contínua agendada das superfícies (não só playlists) quando
  ambos os lados declararem `rw`; superfícies `r` ficam como importação única.
- Capacidade por conta/superfície continuam sendo o gate: nada é importado de
  uma superfície desligada, e nada é apagado no destino sem confirmação.

Segurança:

- `sp_dc` permanece em arquivo `0600` por conta, nunca em logs.
- Backups ZIP sem criptografia não incluem `sp_dc` (default nem nomeados).
- Depois de existir backup criptografado autenticado, a inclusão do cookie será
  uma opção explícita, nunca o padrão silencioso.

### 4. Spotify Web sem Premium + exportação oficial

Fluxo cotidiano:

- ~~Transformar o modo `sp_dc` em backend completo, não somente de escrita.~~
- ~~Listar a `rootlist`, pastas, playlists próprias e playlists salvas.~~
- Importar curtidas, álbuns e artistas seguidos.
- ~~Pesquisar/resolver faixas sem depender do cliente OAuth.~~
- ~~Renovar o token temporário, detectar cookie expirado e mostrar o erro real.~~
- ~~Expor os estados `OAuth oficial`, `Web/cookie` e `Desativado`.~~

Fluxo histórico e de recuperação:

- ~~Importar os JSON/ZIP de dados solicitados ao Spotify.~~
- ~~Trazer playlists e músicas para o banco canônico.~~
- ~~Importar histórico normal e estendido como eventos idempotentes.~~
- ~~Alimentar recaps mensais e anuais sem somar novamente o mesmo play.~~

Segurança:

- `sp_dc` permanece em arquivo `0600`, nunca em logs.
- Backups ZIP sem criptografia não incluem `sp_dc`.
- Depois de existir backup criptografado autenticado, a inclusão do cookie será
  uma opção explícita, nunca o padrão silencioso.

### 5. Banco canônico e superfícies comerciais

- Persistir no banco canônico cada leitura completa feita pelos conectores.
- Implementar capacidades por conta e por superfície.
- Sincronizar curtidas, artistas, álbuns e playlists salvas somente quando os
  dois lados declararem suporte seguro.
- Permitir desligamento independente por conta/superfície e manter logs claros.
