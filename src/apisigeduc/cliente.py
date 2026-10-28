"""Sessão de navegador autenticada no SIGEduc (via Playwright)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from . import seletores as S
from .config import Config
from .usuarios import Credencial

log = logging.getLogger(__name__)


class ErroLogin(RuntimeError):
    pass


class SigeducCliente:
    """Uso:

        with SigeducCliente(config, credencial) as cliente:
            cliente.login()
            cliente.page.goto(...)
    """

    def __init__(
        self, config: Config, credencial: Credencial, pasta_capturas: Path = Path("screenshots")
    ):
        self.config = config
        self.credencial = credencial
        self.pasta_capturas = pasta_capturas
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.page: Page

    def __enter__(self) -> "SigeducCliente":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.config.headless)
        contexto = self._browser.new_context(locale="pt-BR")
        contexto.set_default_timeout(self.config.timeout_ms)
        self.page = contexto.new_page()
        return self

    def __exit__(self, tipo, valor, tb) -> None:
        if valor is not None:
            self.capturar_tela("erro")
        if self._browser:
            self._browser.close()
        if self._pw:
            self._pw.stop()

    def capturar_tela(self, prefixo: str) -> Path | None:
        try:
            self.pasta_capturas.mkdir(exist_ok=True)
            caminho = self.pasta_capturas / f"{prefixo}-{datetime.now():%Y%m%d-%H%M%S}.png"
            self.page.screenshot(path=str(caminho), full_page=True)
            log.info("Captura de tela salva em %s", caminho)
            return caminho
        except Exception:  # captura é só diagnóstico; não deve mascarar o erro original
            log.debug("Falha ao capturar tela", exc_info=True)
            return None

    def login(self) -> None:
        log.info("Acessando %s", self.config.url)
        self.page.goto(self.config.url + S.LOGIN_PATH)
        self.page.fill(S.LOGIN_USUARIO, self.credencial.login)
        self.page.fill(S.LOGIN_SENHA, self.credencial.senha)
        botao = self.page.locator(S.LOGIN_ENTRAR)
        if botao.count() > 0:
            botao.first.click()
        else:
            log.debug("Botão de login não encontrado; enviando o formulário diretamente")
            self.page.locator(S.LOGIN_SENHA).evaluate("campo => campo.form.requestSubmit()")
        self.page.wait_for_load_state("networkidle")

        ainda_no_login = self.page.locator(S.LOGIN_SENHA).count() > 0
        if ainda_no_login and self.page.locator(S.LOGADO).count() == 0:
            erro = self.page.locator(S.LOGIN_ERRO)
            mensagem = erro.first.inner_text().strip() if erro.count() else "motivo desconhecido"
            raise ErroLogin(f"Não foi possível entrar no SIGEduc: {mensagem}")
        log.info("Login realizado como %s", self.credencial.login)

    def pausa(self) -> None:
        self.page.wait_for_timeout(self.config.pausa_ms)
