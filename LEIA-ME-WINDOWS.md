# GetComics — Windows portátil (64 bits)

Esta pasta contém **o projecto Windows completo** e o processo de compilação. A versão para distribuição é o ZIP `dist/GetComics-Windows-Portable.zip`, gerado **num computador Windows** ou pelo GitHub Actions. **Este ZIP de código-fonte não é um executável pré-compilado.**

## Opção 0 — Gerar o EXE no GitHub a partir do teu Mac (automatizado)

Na pasta do ZIP, executa `GERAR_WINDOWS_NO_MAC.command` no Terminal. O script usa o `git` e o `gh` já configurados, coloca os ficheiros **numa branch `windows-portable` separada** do repositório `ivoefcarvalho-netizen/GetComics-WebUI` (não modifica `main`), inicia a compilação Windows na nuvem, acompanha-a e descarrega automaticamente o ZIP compilado para `~/Downloads/GetComics-Windows-Distribuicao`. É necessário ter GitHub CLI autenticado (`gh auth login`) e permissões para actualizar o repositório. Se a compilação não arrancar ou falhar, consultar o GitHub Actions.

**O script envia o código-fonte da aplicação para essa branch pública, mas nunca inclui credenciais.** Não é necessário ter um Windows em casa.

## Opção A — Gerar o ZIP autónomo num PC Windows

Requisitos **apenas para quem compila**: Windows 10/11 de 64 bits, Python **3.12 x64**, Internet e PowerShell.

1. Descomprime este projecto num PC Windows.
2. Na pasta, clica com o botão direito em `build_windows.ps1` e escolhe executar com PowerShell, ou abre PowerShell **nessa pasta** e executa:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
   ```

3. O script instala as dependências **no ambiente virtual local** (não globalmente), obtém o aria2c **oficial**, verifica o SHA-256, executa os testes, compila com PyInstaller e faz um auto-teste ao EXE final.
4. O resultado é `dist\GetComics-Windows-Portable.zip`.
5. Envia **apenas o ZIP compilado** aos amigos. Depois de o descomprimirem, abrem `GetComics\GetComics.exe`.

O ZIP de distribuição contém Python, bibliotecas, interface e aria2c integrados; **os amigos não precisam de instalar Python**. A janela própria usa WebView2 quando disponível. Alguns PCs mais antigos podem precisar do WebView2 Runtime (componente Microsoft), e executáveis não assinados podem mostrar um aviso SmartScreen.

## Opção B — Compilar sem ter um Windows (GitHub Actions)

Coloca o conteúdo completo desta pasta **na raiz de um repositório GitHub** e abre *Actions → GetComics Windows portátil → Run workflow*. Quando a tarefa terminar, descarrega o artefacto `GetComics-Windows-Portable` no final da página da execução.

Ao actualizar o projecto, a automatização também pode compilar na branch `main`.

## Opção C — Testar o código directamente no Windows (exige Python)

Abre `INICIAR_WINDOWS.bat`. Na primeira utilização cria um ambiente virtual, instala as bibliotecas e abre uma janela nativa. **Esta opção não é destinada à distribuição**, pois exige Python já instalado.

## Configurações individuais, todas na interface

| Definição | Valor inicial | Guardada por utilizador? |
|---|---|---|
| Pasta de destino | `Downloads\Comics` | Sim |
| Chave API Pixeldrain | Vazia | Sim, encriptada com Windows DPAPI |
| Prioridade de servidor | Directo → Pixeldrain | Sim |
| Ligações Pixeldrain | 8 | Sim |
| Máximo de páginas de pesquisa | 40 | Sim |
| Ano mínimo | Vazio | Seleccionado na pesquisa |
| Pesquisa por tags | Sem filtro | Seleccionado na pesquisa |
| Apenas números individuais | Activado | Seleccionado na pesquisa |
| Pausa/retoma | Não pausado | Durante a sessão |
| Fila de downloads e estados | Vazia | Sim, automaticamente |

As configurações são guardadas em `%APPDATA%\GetComics Local\settings.json`. A chave é protegida por DPAPI e fica no ficheiro `%APPDATA%\GetComics Local\pixeldrain.dat` (não pode ser utilizada por outras contas Windows). A cache e os registos de arranque ficam nessa pasta. As credenciais **não são incluídas** no ZIP enviado aos amigos. Quem recebe a aplicação deve introduzir a sua própria chave na secção **Definições → Conta Pixeldrain Premium**.

## Nova funcionalidade: importar lista do Secret Stash

A secção **Importar Lista do Secret Stash** permite importar o CSV de desejos, procurar as edições por série/número/ano, seleccionar os resultados e adicioná-los à fila de downloads. A lista fica guardada em `%APPDATA%\GetComics Local\secretstash_wishlist.json`. Consultar `IMPORTAR-SECRET-STASH.md`.

## Funcionalidades

- Pesquisar por título, restringir a tags, filtrar por ano mínimo e por edições individuais.
- Colar uma página com vários volumes e seleccionar ficheiros específicos.
- Escolher pasta de destino através de diálogo nativo Windows.
- Seleccionar várias edições e gerir fila de downloads.
- Pausar/retomar a fila e o processo aria2c em execução (Windows usa `psutil`).
- Tentar fontes alternativas disponíveis, quando o servidor responder com um erro recuperável.
- Integração com Pixeldrain através da chave API pessoal.
- Validar ZIP/CBZ/CBR e não guardar páginas HTML de erro como ficheiros válidos.
- Cache de pesquisa persistente e registos locais.
- **Fila de downloads persistente**: guarda automaticamente as transferências, estados, destinos e fontes em `%APPDATA%\GetComics Local\download_queue.json`.
- Após fechar e voltar a abrir, itens pendentes ou interrompidos reaparecem; seleccionar **Retomar pendentes** para continuar (não reinicia automaticamente).
- **Repetir falhados** na secção Transferências permite repetir todos os erros, ou **Repetir** num item isolado. Ficheiros concluídos mantêm-se registados.
- A chave API permanece exclusivamente no Windows DPAPI, **não** é escrita na fila. As ligações de download podem conter identificadores da fonte; o ficheiro deve ser considerado privado.


**Limitações**: a estrutura HTML do site pode mudar; o acesso à rede e aos servidores pode ser restringido; links que exijam serviços manuais não são descarregados automaticamente; pausas prolongadas podem terminar sessões HTTP. Os testes automatizados não substituem testes reais no Windows e com as fontes utilizadas. Utilizar apenas ficheiros cuja transferência seja autorizada.

## Informação técnica

Python 3.12 + requests + BeautifulSoup + pywebview + PyInstaller + psutil. O servidor HTTP só escuta em `127.0.0.1`, numa porta aleatória. Depois de fechar a janela, o servidor termina. Os registos de arranque são escritos em `%APPDATA%\GetComics Local\startup.log`. O pacote usa modo `onedir`: **manter todos os ficheiros da pasta GetComics juntos**.

A compilação obtém aria2 1.37.0 do repositório oficial e verifica SHA-256 `67D015301EEF0B612191212D564C5BB0A14B5B9C4796B76454276A4D28D9B288` antes de o empacotar. Consultar `AVISOS-TERCEIROS.txt` e garantir o cumprimento das licenças e das obrigações de distribuição dos componentes incluídos.
