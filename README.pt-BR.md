<div align="center"><a name="readme-top"></a>

[English](README.md) · **Português (Brasil)**

# Sync My Music

**Sua central de música self-hosted.** Mantenha uma cópia canônica da sua
biblioteca, sincronize playlists entre serviços, conecte players open source e
centralize seus recaps sem entregar todo o seu histórico a mais uma plataforma
hospedada por terceiros.

Feito para rodar em um computador pessoal, home server ou aparelho com Termux e
ser acessado pela sua rede local.

**Biblioteca canônica · sincronização entre serviços · histórico de versões · exportação para Musify · backup/P2P do Sonora · recaps unificados · logs pesquisáveis**

> [!IMPORTANT]
> O Sync My Music ainda é uma versão inicial para self-hosting. O painel LAN não
> possui login próprio. Não exponha a porta na internet e não configure port
> forwarding. Tokens e credenciais capturadas do navegador devem ser tratados
> como senhas.

O Sync My Music é uma adaptação MIT do
[SongMirror](https://github.com/ahnafnafee/songmirror). O SongMirror fornece a
base madura de matching, transferências e reconciliação de playlists. Este
projeto adiciona banco canônico, recaps, recuperação de playlists, interface
operacional e integrações com [Musify](https://github.com/gokadzev/Musify) e
[Sonora](https://github.com/gmstyle/sonora).

[Instalação](#-instalação-rápida) · [Funcionalidades](#-o-que-ele-faz) · [Integrações](#-integrações) · [Recaps](#-modelo-de-dados-e-recaps) · [Roadmap](#-estado-atual-e-roadmap) · [Arquitetura](docs/architecture.md) · [Sync com upstream](docs/upstream-sync.md)

</div>

## ✨ O que ele faz

### Uma biblioteca que pertence a você

- Banco SQLite canônico para músicas, artistas, álbuns, identidades por
  serviço, playlists, itens ordenados, histórico de reprodução, execuções de
  sync e logs.
- IDs de Spotify, YouTube Music, Apple Music, Sonora e outros catálogos podem
  apontar para a mesma gravação canônica.
- Superfícies independentes para playlists, músicas curtidas, artistas
  seguidos, álbuns/playlists salvos e estatísticas de reprodução.
- Uma superfície não suportada por determinado serviço é ignorada
  explicitamente, nunca interpretada silenciosamente como vazia.

### Sincronização de playlists com recuperação

- Sincronização unidirecional com uma fonte da verdade.
- Reconciliação bidirecional N-way entre vários serviços.
- Vários jobs nomeados, cada um com serviços, playlists, agenda e limites
  próprios.
- Transferências únicas com progresso, pausa, retomada, cancelamento e
  resolução manual de músicas não encontradas.
- Matching por ISRC, links já encontrados em cache e fallback por
  título/artista/duração com suporte a Unicode.
- Versões limitadas de playlists criadas antes de alterações, com preview e
  restauração pela interface.

### Um recap para todos os aplicativos

- Eventos individuais deduplicados enviados por um endpoint compatível com
  ListenBrainz.
- Snapshots mensais substituíveis importados manualmente do Musify e Sonora.
- Visões mensais e anuais combinadas.
- O projeto nunca inventa ou escreve histórico de reprodução de volta nos
  serviços comerciais.

### Operação transparente

- Cada serviço pode ser pausado sem apagar os dados já importados.
- Os 5.000 logs estruturados mais recentes podem ser pesquisados e filtrados
  pela interface.
- Escritas possuem preview, limites de adições/remoções e proteções contra
  leituras incompletas.
- Pode rodar via Docker, Python diretamente ou em um servidor Termux sempre
  ligado.

## 🔌 Integrações

| Serviço ou app | Playlists | Outras superfícies | Autenticação ou transporte |
| --- | --- | --- | --- |
| **Spotify** | Leitura e escrita | Matching de catálogo | OAuth; modo de escrita `sp_dc` opcional |
| **YouTube Music** | Leitura e escrita | Matching de catálogo | OAuth da Data API ou sessão do navegador |
| **Apple Music** | Leitura e escrita | Metadados de catálogo | Bearer + Media-User-Token |
| **TIDAL** | Leitura e escrita | Metadados de catálogo | Bearer do web player |
| **Qobuz** | Leitura e escrita | Metadados de catálogo | Headers da API do web player |
| **Deezer** | Leitura e escrita | Metadados de catálogo | Sessão web renovável por `refresh-token` |
| **Amazon Music** | Leitura e escrita | Metadados de catálogo | Sessão web renovável e minimizada |
| **Jellyfin** | Navegação/espelho local | Capas e biblioteca local | URL do servidor + API key |
| **Musify** | Exportação por custom link | Importação substituível de recap | Integração manual; nenhuma senha do Musify é armazenada |
| **Sonora** | Backup de playlists e entradas locais | Curtidas, artistas, álbuns e histórico | ZIP backup-v2 ou P2P pareado na LAN |

> [!WARNING]
> Conectores baseados no navegador usam interfaces internas dos próprios
> serviços. Eles podem quebrar quando um web player muda. Por isso, cada
> provedor possui desligamento independente e seus dados locais são preservados.

### Integração com Musify

O [Musify](https://github.com/gokadzev/Musify) continua sendo um aplicativo
independente. O Sync My Music não modifica diretamente seu banco Hive e não
simula o funcionamento interno do app.

- Uma playlist de qualquer serviço legível é convertida para IDs do YouTube
  Music e exportada como `musify://playlist/custom/...`.
- Estatísticas wrapped/listening do Musify podem ser importadas para o recap
  unificado.
- Cada mês importado é um snapshot substituível. Se julho for importado primeiro
  com 20 minutos e depois com 40, o resultado será **40**, nunca 60.

### Integração com Sonora

O adaptador do [Sonora](https://github.com/gmstyle/sonora) entende suas
estruturas backup-v2 e pode trocar mais do que playlists:

- Músicas curtidas, artistas seguidos, álbuns e playlists curtidas.
- Playlists locais e a ordem de suas entradas.
- Histórico de reprodução importado como snapshots mensais substituíveis.
- Importação e exportação de backup ZIP.
- Descoberta e sincronização opcional entre aparelhos pela LAN.

A descoberta LAN começa desligada. Quando ativada, o pareamento exige PIN e o
usuário escolhe quais superfícies serão sincronizadas. Histórico de pesquisa e
configurações do Sonora não entram no banco canônico.

## 🧠 Modelo de dados e recaps

`data/sync_music.db` é o banco principal do produto. As bibliotecas dos
provedores são espelhos do estado externo; as entidades canônicas são a
representação local durável.

O histórico de reprodução possui duas semânticas diferentes:

- **Eventos** representam reproduções individuais. São adicionados uma única vez
  e deduplicados pelo ID de origem ou fingerprint.
- **Snapshots** representam totais exportados manualmente. Importar novamente o
  mesmo serviço/mês substitui o snapshot anterior.

O cache de matching original continua separado. Isso evita misturar resultados
temporários de busca em catálogos com o histórico pessoal permanente.

Veja [docs/architecture.md](docs/architecture.md) para o schema completo,
superfícies, regras de segurança de playlists e protocolo P2P do Sonora.

## 🚧 Estado atual e roadmap

Já implementado:

- Banco canônico e página de biblioteca.
- Engine de playlists, matching, transferências e jobs nomeados do SongMirror.
- Histórico e recuperação de versões de playlists.
- Exportação de playlists e importação de recaps do Musify.
- Backup-v2 e pareamento/sincronização LAN configurável do Sonora.
- Recaps unificados, logs persistentes e pausa por provedor.
- Histórico mensal/anual com retenção configurável de 1 a 10 anos.
- Backup e restauração versionados do próprio SYNC; o cookie `sp_dc` fica fora
  dos ZIPs enquanto não houver criptografia.

Em evolução:

- Adaptadores automáticos de curtidas, artistas seguidos e álbuns salvos para
  todos os serviços comerciais.
- Configuração completa de várias contas nos conectores herdados.
- Login e autorização adequados para acesso fora de uma LAN confiável.
- Mais formatos de importação, ferramentas de conflito e testes de integração.
- Tradução pt-BR completa da interface web.

Uma matriz honesta do que está completo, parcial ou somente preparado no schema
fica em [Estado real e fila do produto](docs/plans/product-backlog.pt-BR.md).

## 🚀 Instalação rápida

### Docker

```bash
git clone https://github.com/elias001011/sync-my-music.git
cd sync-my-music
docker compose up -d --build
```

Abra `http://localhost:8888`. Para acessar pela rede doméstica, use
`http://IP-DO-SERVIDOR:8888`.

O diretório `./data` contém banco, versões, recaps, logs, credenciais, tokens e
caches. Faça backup dele antes de reinstalar ou migrar o servidor.

### Execução direta

```bash
uv sync
uv run uvicorn songmirror.web:app --host 0.0.0.0 --port 8080
```

Requer Python 3.13+ e [`uv`](https://docs.astral.sh/uv/). Não encaminhe essa
porta para a internet.

## ⚙️ Como a sincronização funciona

Em cada execução:

1. O sistema captura um snapshot da playlist de origem.
2. Encontra a playlist equivalente em cada destino conectado.
3. Resolve músicas por cache, ISRC e busca pontuada.
4. Calcula adições e remoções.
5. Aplica somente as mudanças permitidas pelos limites e proteções.
6. Salva snapshots limpos para reconciliação futura e recuperação.

No modo N-way, cada provedor é comparado com o último estado canônico conhecido.
As mudanças são combinadas e propagadas sem eco. Em conflitos, adição vence por
padrão, pois perder uma música é normalmente pior do que manter uma música extra.

## 🛡️ Proteções

- Preview/dry-run antes de escritas reais.
- Remoções desligadas por padrão.
- Limites separados para adições e remoções.
- Uma leitura vazia inesperada nunca esvazia automaticamente um destino.
- Quedas grandes de leitura fazem aquele provedor ser ignorado na execução.
- Falhas de autenticação interrompem o provedor antes de alterações parciais.
- O estado atual é versionado antes de uma restauração de playlist.
- A sincronização LAN do Sonora exige pareamento por PIN.

## 🔑 Credenciais

O painel de **Contas** orienta a autenticação de cada serviço. O projeto nunca
solicita diretamente a senha da sua conta:

- Spotify usa OAuth oficial, com modo `sp_dc` opcional para escrita.
- YouTube Music usa OAuth da Data API ou cookies do navegador.
- TIDAL, Qobuz e Apple Music usam tokens/headers capturados do web player e
  precisam ser recapturados quando expirarem.
- Deezer e Amazon Music guardam apenas o subconjunto de cookies necessário para
  renovação da sessão.
- Jellyfin usa API key.

Arquivos de configuração sensíveis são criados com permissão `0600` e o
diretório de dados tenta aplicar `0700` em sistemas POSIX. Mesmo assim, trate
todo o diretório `data` como secreto.

## 🧱 Estrutura do projeto

```text
songmirror/
  engine/       # matching, reconciliação, providers e downloads
  services/     # contas, banco, playlists, recaps, Sonora, transferências e logs
  web/          # API FastAPI e eventos SSE
frontend/       # aplicação React + Vite
data/           # dados de runtime; ignorados pelo Git
docs/           # arquitetura e semântica de sincronização
```

## 🤝 Créditos

- [SongMirror](https://github.com/ahnafnafee/songmirror), criado por
  [Ahnaf An Nafee](https://github.com/ahnafnafee), é a base dos conectores,
  matching, reconciliação, transferências e grande parte da interface original.
- [Musify](https://github.com/gokadzev/Musify) define/inspira o custom link de
  playlists e a superfície de importação manual.
- [Sonora](https://github.com/gmstyle/sonora) define as estruturas backup-v2 e a
  superfície de sincronização entre aparelhos utilizada pela integração.

O Sync My Music é uma adaptação comunitária independente e não possui afiliação
com os serviços comerciais mencionados nem com os mantenedores do Musify e
Sonora.

## 📄 Licença

Distribuído sob a [Licença MIT](LICENSE). O aviso de copyright e a licença do
SongMirror original são preservados. Novas contribuições do Sync My Music usam a
mesma licença, salvo indicação contrária.
