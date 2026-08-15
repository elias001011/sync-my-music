# Estado real e fila do produto

Este arquivo separa o que já funciona do que existe apenas como fundação. Ele
evita que uma tabela criada no banco seja confundida com uma integração pronta
na interface e nos conectores.

## Matriz atual

| Área | Estado | Limite atual |
| --- | --- | --- |
| Jobs nomeados de playlists | Implementado | Selecionam provedores, não contas individuais. |
| Pareamento explícito de playlists | Implementado | Opera sobre um único login por provedor. |
| Transferência entre serviços | Implementado | Playlists; snapshots nomeados de Musify/Spotify/outros backups de conta podem ser origem manual. |
| Versões de playlists | Implementado | Histórico limitado por playlist/provedor. |
| Musify | Implementado/parcial | Importa `user.hive`, superfícies e recap; exporta playlists por link. A atualização continua manual. |
| Sonora | Implementado/parcial | Backup-v2 e bridge LAN pareada; depende da compatibilidade do protocolo do app. |
| Recap unificado | Implementado | Entrada por eventos e snapshots substituíveis; não escreve histórico nos serviços. |
| Banco canônico | Parcial | Musify, Sonora e listens o alimentam; passes comerciais ainda usam também o archive/cache herdado. |
| Curtidas, álbuns e artistas | Parcial | Modelo canônico e adapters Musify/Sonora existem; faltam adapters completos nos serviços comerciais. |
| Múltiplas contas no mesmo serviço | Parcial | Imports/restores nomeados são isolados, têm backup próprio e podem alimentar transferências. Perfis simultâneos de credenciais em jobs ainda faltam. |
| Spotify totalmente sem OAuth | Implementado/parcial | `sp_dc` cobre rootlist/pastas, leitura, busca e escrita. Curtidas/álbuns/artistas contínuos ainda dependem de export oficial. |
| Backup do próprio SYNC | Implementado | ZIP versionado e validado; `sp_dc` é excluído até existir criptografia. |

## Próximos blocos

### 1. Múltiplas contas por serviço

- Criar perfis de credenciais isolados para contas **ao vivo**; IDs estáveis e
  slots isolados de restore/import já existem.
- Fazer Accounts adicionar, renomear, pausar e remover cada perfil.
- Trocar seletores de jobs, links e transferências de `provider` para
  `account_id`.
- Isolar tokens, caches e sessões por conta.
- Migrar a conta única existente para `<provider>:default` sem perda.
- A separação e deduplicação de recaps por conta já existe; falta o filtro de
  combinação/seleção na visualização.

Não será adicionada uma UI que apenas pareça multi-conta: o recurso só fica
pronto quando leitura, escrita, scheduler, transferências e backup respeitarem o
perfil selecionado.

### 2. Spotify Web sem Premium + exportação oficial

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

### 3. Banco canônico e superfícies comerciais

- Persistir no banco canônico cada leitura completa feita pelos conectores.
- Implementar capacidades por conta e por superfície.
- Sincronizar curtidas, artistas, álbuns e playlists salvas somente quando os
  dois lados declararem suporte seguro.
- Permitir desligamento independente por conta/superfície e manter logs claros.
