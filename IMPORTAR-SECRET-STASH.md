# Secret Stash — importação da lista de desejos (Windows)

**Novo na versão Secret Stash:** na interface existe uma secção **«Importar Lista do Secret Stash»**.

1. Carrega em **Importar CSV** e escolhe o ficheiro exportado do Secret Stash.
2. A aplicação lê localmente o ficheiro `Série;Ano;Editora;Número;Tipo;…` (separado por ponto e vírgula e em UTF-8). Os campos obrigatórios são `Série`, `Ano` e `Número`.
3. Carrega em **Pesquisar lista** para procurar as edições. A pesquisa corre por **série**, evitando uma pesquisa para cada número.
4. A tabela indica **Encontrado**, **Não encontrado**, **Erro** ou **Pendente**. Apenas correspondências com **série + número + ano** são seleccionáveis. Clica no título encontrado para abrir o artigo e confirmar.
5. Selecciona os encontrados e carrega em **Descarregar seleccionados**. Passam para a fila normal, com pausa, retoma, fontes alternativas e persistência já existentes.
6. Para voltar a verificar os títulos por encontrar, carrega em **Pesquisar lista**. Para ignorar a cache e procurar tudo de novo, usa **Pesquisar novamente**.

**A lista importada fica guardada** fora da pasta do programa, em `%APPDATA%\GetComics Local\secretstash_wishlist.json`.
As pesquisas não são retomadas automaticamente depois de fechar a aplicação; podes iniciá-las mais tarde com o botão. Não se incluem chaves API nem o CSV pessoal no pacote distribuído. Cada amigo importa a sua própria lista.

**Limitações:** o site pode não apresentar todas as edições ou mostrar algumas apenas dentro de páginas com vários livros; nesses casos, a correspondência automática conservadora pode não as encontrar. Não são feitos downloads sem selecção do utilizador. Se houver limite HTTP 429, interrompe a pesquisa até o servidor permitir novas consultas. Apenas utilizar fontes e conteúdos cuja transferência seja autorizada.

## Compilar no Windows

Descomprime este projecto numa pasta local (de preferência fora do OneDrive). Abre o PowerShell na pasta `GetComics-Windows` e executa:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```

Requer Python 3.12 x64 para a **compilação**, mas os utilizadores finais não precisam de Python instalado.
O ZIP distribuível será criado em `dist\GetComics-Windows-Portable.zip`, com o `GetComics.exe` e a pasta `_internal`. Fecha eventuais instâncias da aplicação antes de compilar.

O novo módulo `secretstash.py` e o teste `tests/test_secretstash.py` estão incluídos na compilação. O `build_windows.ps1` executa todos os testes antes de empacotar.
