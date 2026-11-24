# Eventos Internos

Sistema simples (Flask, sem React) para a rede interna: upload de fotos e vídeos
de eventos, com data, hora e local. Os arquivos são salvos automaticamente em
`uploads/<data-do-evento>/`, então tudo fica organizado por pasta de data.

## Como rodar

```bash
cd eventos_internos
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

O servidor sobe em `http://0.0.0.0:5000`. Qualquer computador da rede interna
acessa pelo IP da máquina que está rodando, por exemplo `http://192.168.0.10:5000`.

## Estrutura

- `app.py` — servidor Flask (rotas de upload, galeria e download dos arquivos).
- `templates/` — páginas HTML (formulário e galeria).
- `static/style.css` — visual do sistema.
- `uploads/AAAA-MM-DD/` — pastas criadas automaticamente para cada data de evento.
- `eventos.json` — lista de eventos (data, hora, local e arquivos), usada pela galeria.

## Como funciona

1. Na página inicial, o usuário escolhe a **data**, **hora** (opcional) e **local**
   do evento e seleciona uma ou mais imagens/vídeos.
2. Ao enviar, os arquivos são salvos em `uploads/<data escolhida>/`.
3. A página **Galeria** lista todos os eventos agrupados por data, do mais
   recente para o mais antigo, mostrando local, hora, fotos e vídeos.

## Acesso simultâneo (várias pessoas ao mesmo tempo)

O servidor já está configurado com `threaded=True`, o que permite atender
várias pessoas enviando arquivos ao mesmo tempo (testado para o cenário de
5-6 usuários simultâneos). Além disso, a escrita do `eventos.json` é
protegida por um lock, evitando que dois uploads simultâneos "atropelem" o
registro um do outro.

Para uso do dia a dia (fora de testes), é recomendado **não** deixar
`debug=True` ligado e usar um servidor WSGI mais robusto em vez do servidor
de desenvolvimento do Flask. Duas opções simples:

```bash
pip install waitress
```
```python
# no lugar do app.run(...), crie um arquivo run_producao.py:
from waitress import serve
from app import app
serve(app, host="0.0.0.0", port=5000, threads=8)
```

Isso deixa o sistema mais estável para ficar no ar continuamente na rede
interna, atendendo múltiplos acessos sem os avisos/recarregamentos do modo
debug.

## Possíveis próximos passos

- Autenticação simples (login) antes de permitir upload.
- Limite de tamanho de arquivo (`MAX_CONTENT_LENGTH` no Flask).
- Excluir eventos/arquivos pela galeria.
