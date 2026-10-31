# Fila persistente e repetição de downloads

## Novos botões

Na secção **Transferências**:
- **↻ Repetir falhados** — volta a colocar na fila todos os itens que terminaram com erro.
- **↻ Repetir** — tenta novamente apenas o item escolhido.
- **▶ Retomar pendentes** — agenda os itens que ficaram por fazer ou foram interrompidos ao fechar a aplicação.
- **⏸ Pausar downloads / ▶ Retomar downloads** — controlo da sessão em curso.

Os itens não são reiniciados automaticamente ao abrir, para evitar pedidos inesperados ou sucessivos erros HTTP 429.

## O que fica guardado

A fila é gravada automaticamente em `%APPDATA%\GetComics Local\download_queue.json` e inclui:
- título, estado e número de tentativas;
- fontes de transferência encontradas;
- pasta de destino original;
- nome do ficheiro concluído, quando aplicável.

A chave Pixeldrain **não** é gravada na fila: mantém-se separada, protegida pelo Windows DPAPI. As ligações da fila podem conter identificadores de acesso de terceiros; não publiques o ficheiro de estado.

Se o programa for fechado durante uma transferência, essa entrada passa a **Interrompido**. Quando a aplicação reabrir, carrega em **Retomar pendentes**. A preservação dos dados parciais depende do motor e do servidor; algumas transferências reiniciarão do zero.

## Como gerar o EXE no Mac

O ZIP contém **código-fonte**, não um executável pré-compilado. Na pasta do projecto, usa `GERAR_WINDOWS_NO_MAC.command` para iniciar uma compilação num agente Windows do GitHub. O ZIP compilado será descarregado para `~/Downloads/GetComics-Windows-Distribuicao` se a compilação passar.

## Verificação

Executar `python -m unittest discover -s tests -p 'test_*.py' -v` na pasta com Python e dependências instaladas. Os novos testes abrangem gravação atómica, recuperação, privacidade dos dados devolvidos pela API, repetição de erros e encerramento com downloads pendentes.
