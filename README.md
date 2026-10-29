# GetComics — Compilação automática macOS + Windows

Este projecto usa a interface V4 e os módulos de download personalizados da instalação do Mac. **Não inclui a chave API Pixeldrain**.

## Antes de enviar para GitHub (executar no Mac)

1. Extrair o ZIP e abrir o Terminal na pasta `GetComics_GitHub_Actions`.
2. Executar: `./preparar_fontes.sh ~/getcomics-downloader`
3. Verificar que existem na raiz: `getinfo.py`, `download.py`, `menu.py`, `pixeldrain_support.py`.
4. **Rever os ficheiros para garantir que não contêm passwords ou chaves API**. Não incluir `.env`, `pixeldrain.key`, `settings.json` ou pastas de downloads.
5. Criar um repositório **privado** no GitHub, pelo menos enquanto se valida a compilação.
6. Carregar o conteúdo desta pasta no repositório (incluindo `.github/workflows/build.yml`).
7. No GitHub ir a **Actions → Compilar GetComics → Run workflow**.
8. No fim, abrir a execução e descarregar os dois Artifacts: `GetComics-macOS` e `GetComics-Windows`.

Para publicar versões, criar uma tag `v1.0.0`; nesse caso a workflow tenta publicar também os ZIP numa Release.

## Notas

- As builds usam PyInstaller no próprio macOS/Windows; não é compilação cruzada.
- O Python, os módulos e o executável aria2c são incluídos. **Não foi ainda testado num Mac/Windows limpo**, e a distribuição do aria2c pode exigir incluir bibliotecas nativas adicionais conforme o runner.
- No macOS uma aplicação não assinada pode gerar um aviso do Gatekeeper. Para distribuição pública é recomendável assinatura e notarização da Apple.
- No Windows o executável não assinado pode mostrar um aviso do SmartScreen.
- Os utilizadores configuram a própria pasta de downloads e chave API na interface V4. Nunca distribuir a chave privada.
- O servidor web da aplicação está limitado a `127.0.0.1`.
- A aplicação utiliza o serviço GetComics e os respectivos links; respeita os direitos de distribuição dos conteúdos.
