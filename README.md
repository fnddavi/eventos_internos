# Eventos Internos

Sistema leve em Flask para uso exclusivo em **rede interna confiável (LAN)**: upload e galeria de fotos e vídeos de eventos com data, horário (opcional) e local. Todos os dados são persistidos localmente em disco (`eventos.json` e `uploads/AAAA-MM-DD/`), sem banco de dados externo.

> **Aviso de segurança (rede interna sem login):**
> Esta aplicação **não possui autenticação**. Qualquer dispositivo que consiga alcançar o endereço e a porta configurados poderá enviar arquivos e visualizar a galeria.
> **Nunca exponha o serviço à internet**, não configure redirecionamento de portas (*port forwarding*) no roteador e restrinja o acesso via endereço de bind da LAN e firewall do Linux.

## Estrutura do projeto

- `app.py` — fábrica da aplicação Flask (`create_app`), validação de configuração, validação de entradas/uploads, escrita atômica do `eventos.json` e cabeçalhos de segurança.
- `wsgi.py` — ponto de entrada de produção usando **Waitress**.
- `templates/` — páginas HTML (`base.html`, `index.html`, `galeria.html`, `erro.html`).
- `static/style.css` — estilos da interface.
- `deploy/eventos-internos.service` — unidade `systemd` endurecida para execução contínua em Linux.
- `deploy/eventos-internos.env.example` — modelo de configuração de produção.
- `deploy/backup.sh` e `deploy/restore.sh` — scripts de backup consistente (com trava compartilhada e checksum SHA-256) e restauração segura.
- `deploy/eventos-internos-backup.service` e `deploy/eventos-internos-backup.timer` — agendamento diário de backup via `systemd`.
- `tests/` — testes automatizados (configuração de produção, uploads, galeria, segurança e backup/restauração).

## Configuração por variáveis de ambiente

| Variável | Padrão seguro | Descrição |
| --- | --- | --- |
| `EVENTOS_ENV` | `production` | `production`, `development` ou `testing`. |
| `EVENTOS_DEBUG` | `0` | Deve permanecer `0` em produção (o serviço recusa iniciar se `EVENTOS_DEBUG=1` em produção). |
| `EVENTOS_SECRET_KEY` | *(obrigatório em produção)* | Chave secreta com no mínimo 32 caracteres. Gere com `python3 -c 'import secrets; print(secrets.token_hex(32))'`. |
| `EVENTOS_HOST` | `127.0.0.1` | IP onde o Waitress escuta. Em LAN, configure o IP interno fixo do servidor (ex.: `192.168.1.50`). |
| `EVENTOS_PORT` | `5000` | Porta TCP (`1..65535`). |
| `EVENTOS_THREADS` | `8` | Número de threads do Waitress (`1..128`). |
| `EVENTOS_DATA_DIR` | diretório do projeto | Diretório base onde ficam `eventos.json`, `uploads/` e `.eventos.lock`. |
| `EVENTOS_MAX_UPLOAD_MB` | `100` | Tamanho máximo por envio em MB (`1..4096`). |
| `EVENTOS_MAX_FILES_PER_UPLOAD` | `20` | Quantidade máxima de arquivos por envio (`1..500`). |
| `EVENTOS_SESSION_COOKIE_SECURE` | `0` | Defina `1` apenas se houver proxy reverso HTTPS interno na frente do serviço. |
| `EVENTOS_LOG_LEVEL` | `INFO` | Nível de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |

## Execução rápida em desenvolvimento

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
EVENTOS_ENV=development EVENTOS_HOST=127.0.0.1 .venv/bin/python app.py
```

Em modo `development`, se `EVENTOS_SECRET_KEY` não for definida, uma chave efêmera aleatória é gerada automaticamente a cada execução (sem segredo fixo no código).

## Implantação em produção local (Linux + Waitress + systemd)

### 1. Criar usuário de sistema e diretórios

```bash
sudo useradd --system --home /opt/eventos_internos --shell /usr/sbin/nologin eventos
sudo mkdir -p /opt/eventos_internos /etc/eventos-internos /var/lib/eventos-internos /var/backups/eventos-internos
```

### 2. Instalar código e dependências

Copie o repositório para `/opt/eventos_internos` e crie o ambiente virtual:

```bash
sudo cp -a . /opt/eventos_internos/
sudo chown -R root:eventos /opt/eventos_internos
sudo chmod -R u=rwX,g=rX,o= /opt/eventos_internos

sudo -u eventos python3 -m venv /opt/eventos_internos/.venv
sudo -u eventos /opt/eventos_internos/.venv/bin/pip install -r /opt/eventos_internos/requirements.txt
```

### 3. Preservar e migrar dados existentes (`eventos.json` e `uploads/`)

Os dados existentes são 100% compatíveis com o formato atual. Se você já possui `eventos.json` e `uploads/` no diretório atual do projeto:

- **Opção A (recomendada — separar dados em `/var/lib/eventos-internos`):**

```bash
# Copie preservando timestamps e atributos (não apague a origem antes de validar!)
[[ -f eventos.json ]] && sudo cp -a eventos.json /var/lib/eventos-internos/
[[ -d uploads ]] && sudo cp -a uploads /var/lib/eventos-internos/

sudo chown -R eventos:eventos /var/lib/eventos-internos
sudo find /var/lib/eventos-internos -type d -exec chmod 750 {} +
sudo find /var/lib/eventos-internos -type f -exec chmod 640 {} +
```

  *Retorno (rollback) caso precise voltar ao diretório original:* pare o serviço (`sudo systemctl stop eventos-internos`), aponte `EVENTOS_DATA_DIR` de volta ao diretório original (e ajuste `ReadWritePaths` na unidade `systemd`) ou copie `/var/lib/eventos-internos/{eventos.json,uploads}` de volta preservando atributos com `cp -a`.

- **Opção B (manter dados no próprio diretório do projeto):**
  Defina `EVENTOS_DATA_DIR=/opt/eventos_internos` no arquivo `.env`, ajuste `ReadWritePaths=/opt/eventos_internos` em `eventos-internos.service` e dê permissão de escrita ao usuário `eventos` em `eventos.json`, `uploads/` e `.eventos.lock`.

### 4. Configurar variáveis de ambiente e segredo

```bash
sudo cp /opt/eventos_internos/deploy/eventos-internos.env.example /etc/eventos-internos/eventos-internos.env
sudo chown root:eventos /etc/eventos-internos/eventos-internos.env
sudo chmod 640 /etc/eventos-internos/eventos-internos.env
```

Gere uma chave secreta forte e edite `/etc/eventos-internos/eventos-internos.env`:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
sudo nano /etc/eventos-internos/eventos-internos.env
```

Preencha obrigatoriamente:
- `EVENTOS_SECRET_KEY=<chave gerada>`
- `EVENTOS_HOST=<IP fixo deste servidor na LAN, ex.: 192.168.1.50>`

### 5. Instalar e iniciar o serviço `systemd`

```bash
sudo cp /opt/eventos_internos/deploy/eventos-internos.service /etc/systemd/system/eventos-internos.service
sudo systemctl daemon-reload
sudo systemctl enable --now eventos-internos
```

Verifique o estado, a saúde e os logs:

```bash
sudo systemctl status eventos-internos
curl -fsS http://192.168.1.50:5000/health
sudo journalctl -u eventos-internos -n 50 --no-pager
```

### 6. Restringir acesso à rede interna no firewall

Substitua `192.168.1.0/24` pela sub-rede real da sua LAN:

**Com UFW (Ubuntu/Debian):**

```bash
sudo ufw allow from 192.168.1.0/24 to any port 5000 proto tcp comment 'Eventos Internos LAN'
sudo ufw deny 5000/tcp
sudo ufw status verbose
```

**Com nftables (exemplo de regra na chain `input`):**

```bash
sudo nft add rule inet filter input ip saddr 192.168.1.0/24 tcp dport 5000 accept
sudo nft add rule inet filter input tcp dport 5000 drop
```

## Operação diária: reinício, logs e atualização

- **Reiniciar o serviço** (ex.: após alterar `/etc/eventos-internos/eventos-internos.env`):
  ```bash
  sudo systemctl restart eventos-internos
  ```
- **Acompanhar logs operacionais em tempo real** (erros são registrados no `journald` sem expor stack traces ou caminhos internos ao usuário):
  ```bash
  sudo journalctl -u eventos-internos -f
  ```
- **Atualizar o código sem perder dados:**
  ```bash
  # 1. Faça um backup antes de atualizar
  sudo -u eventos /opt/eventos_internos/deploy/backup.sh

  # 2. Atualize os arquivos do projeto e dependências
  cd /opt/eventos_internos
  sudo -u eventos /opt/eventos_internos/.venv/bin/pip install -r requirements.txt

  # 3. Execute os testes automatizados
  sudo -u eventos /opt/eventos_internos/.venv/bin/python -m unittest discover -s tests -v

  # 4. Reinicie o serviço e valide o endpoint de saúde
  sudo systemctl restart eventos-internos
  curl -fsS http://<IP-DA-LAN>:5000/health
  ```

## Backup e restauração consistentes

O script `deploy/backup.sh` usa uma trava compartilhada (`flock -s`) sobre `.eventos.lock` para evitar inconsistências durante uploads simultâneos, gera um arquivo `.tar.gz` com permissão restrita (`0600`) acompanhado do checksum `.sha256` e remove backups antigos conforme `RETENTION_DAYS` (padrão: 14 dias).

### Backup manual

```bash
sudo chown eventos:eventos /var/backups/eventos-internos
sudo chmod 750 /var/backups/eventos-internos
sudo -u eventos /opt/eventos_internos/deploy/backup.sh /var/lib/eventos-internos /var/backups/eventos-internos
```

### Agendamento diário automático (`systemd` timer)

```bash
sudo cp /opt/eventos_internos/deploy/eventos-internos-backup.service /etc/systemd/system/
sudo cp /opt/eventos_internos/deploy/eventos-internos-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now eventos-internos-backup.timer
sudo systemctl list-timers eventos-internos-backup.timer
```

### Restauração segura

O script `deploy/restore.sh` verifica se o serviço está parado, valida o checksum SHA-256, bloqueia entradas maliciosas no arquivo compactado, valida o JSON e salva uma cópia pré-restauração em `.pre-restore-<timestamp>` antes de substituir os arquivos:

```bash
sudo systemctl stop eventos-internos
sudo /opt/eventos_internos/deploy/restore.sh /var/backups/eventos-internos/eventos-internos-AAAAMMDD-HHMMSS.tar.gz /var/lib/eventos-internos
sudo chown -R eventos:eventos /var/lib/eventos-internos
sudo systemctl start eventos-internos
curl -fsS http://<IP-DA-LAN>:5000/health
```

## Testes automatizados

Execute toda a suíte de validação local (configuração de produção, limites/validações de upload, proteção contra path traversal, concorrência, integração com Waitress e backup/restauração):

```bash
.venv/bin/python -m unittest discover -s tests -v
```
