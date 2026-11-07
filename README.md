# Accdc - Astronomy CCD Calculator

<p align="center">
  <img src="assets/logo.png" alt="Accdc Logo" width="128"/>
</p>

<p align="center">
  <strong>Calculadora astronómica de Campo de Visão (FOV) e Escala de Imagem para Astrofotografia</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=flat&logo=python&logoColor=white" alt="Python Version" />
  <img src="https://img.shields.io/badge/GUI-PySide6%20(Qt)-41CD52?style=flat&logo=qt&logoColor=white" alt="PySide6 GUI" />
  <img src="https://img.shields.io/badge/Astro-Astropy%20%7C%20Astroquery-FF5A36?style=flat" alt="Astropy" />
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License" />
</p>

---

## 🔭 Sobre o Projeto

O **Accdc** (*Astronomy CCD Calculator*) é uma aplicação desktop desenvolvida em Python e PySide6 projetada para astrónomos amadores e astrofotógrafos. O programa permite simular e visualizar com precisão como um objeto celeste (nebulosas, galáxias, enxames, etc.) irá enquadrar no sensor da sua câmara acoplada ao seu telescópio.

Além dos cálculos óticos, o software consulta catálogos astronómicos (SIMBAD/NED via Astroquery) e descarrega automaticamente imagens reais de levantamentos celestes (*Sky Surveys* como DSS/STScI), projetando o retângulo do campo de visão (FOV) do seu equipamento com suporte a rotação, deslocamento (*pan/drag*), inversão (*flip*) e zoom.

---

## ✨ Funcionalidades Principais

- **Cálculos Óticos em Tempo Real:**
  - Escala de imagem (arcsec/pixel).
  - Campo de Visão (FOV) em minutos de arco ($'$) e graus ($^\circ$).
  - Razão focal ($f/\#$).
- **Visualizador de FOV Interativo:**
  - Projeção do enquadramento do sensor sobre imagens reais do céu descarregadas automaticamente.
  - Ajuste de rotação do sensor (ângulo da câmara).
  - Inversão horizontal e vertical (*Flip H / Flip V*) para coincidir com diferentes configurações óticas (refratores, Newtonianos, diagonais).
  - Zoom e arrasto interativo (*pan*) do campo de visão.
- **Gestão de Equipamento:**
  - Registo e seleção rápida de telescópios (abertura e distância focal).
  - Registo e seleção rápida de câmaras CCD/CMOS (resolução em píxeis e tamanho do píxel em $\mu m$).
- **Pesquisa de Objetos Celestes:**
  - Resolução de nomes de alvos astronómicos através do SIMBAD/Astroquery (Messier, NGC, IC, etc.) obtendo automaticamente as coordenadas RA e Dec.
- **Histórico e Rede:**
  - Registo local do histórico de alvos pesquisados.
  - Aba *Network* para partilha e consulta de registos e alvos observados com a comunidade via Firebase.

---

## 🛠️ Tecnologias Utilizadas

- **Linguagem:** Python 3.10+
- **Interface Gráfica:** [PySide6](https://pypi.org/project/PySide6/) (Qt 6 para Python)
- **Cálculos Astronómicos e Consultas:** [Astropy](https://www.astropy.org/) e [Astroquery](https://astroquery.readthedocs.io/)
- **Processamento de Imagem:** [Pillow (PIL)](https://python-pillow.org/)
- **Sincronização:** Firebase Realtime Database REST API

---

## 🚀 Instalação e Execução

### 1. Clonar o Repositório
```bash
git clone https://github.com/ruirigel/accdc.git
cd accdc
```

### 2. Criar e Ativar um Ambiente Virtual
```bash
# Windows
python -m venv .venv
.\.venv\Scripts\activate

# Linux / macOS
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Instalar Dependências
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Executar a Aplicação
```bash
python -m ui.main_window
# ou
python create_release.py
```

---

## 📦 Gerar Executável (Standalone)

Para compilar um ficheiro executável autónomo para Windows utilizando o [PyInstaller](https://pyinstaller.org/):

```bash
pyinstaller main_window.spec
```
O executável compilado será colocado na pasta `dist/`.

---

## 📂 Estrutura do Projeto

```text
accdc/
├── assets/             # Ícones e recursos gráficos da aplicação
├── core/               # Lógica de cálculo, consultas astronómicas e sincronização
│   ├── calculator.py   # Fórmulas óticas (escala, FOV, focal ratio)
│   ├── firebase_sync.py# Sincronização em rede
│   ├── query.py        # Consultas astronómicas e download de imagens DSS
│   ├── storage.py      # Gestão de definições, histórico e instrumentos
│   └── version.py      # Controlo de versão
├── data/
│   └── instruments.json# Lista base de instrumentos (telescópios e câmaras)
├── ui/
│   └── main_window.py  # Interface gráfica principal em PySide6
├── .gitignore          # Ficheiros ignorados pelo Git
├── create_release.py   # Script auxiliar de release
├── main_window.spec    # Especificação de build do PyInstaller
├── requirements.txt    # Dependências do projeto
└── README.md           # Documentação do projeto
```

---

## 👤 Autor

Desenvolvido por **Rui Branco** ([@ruirigel](https://github.com/ruirigel)).
