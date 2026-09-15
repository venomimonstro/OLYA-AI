from __future__ import annotations

from ..exceptions import ChromeRuntimeException, ChromeUserAbortException
from ..logger import logger
from ..parser import get_parser
from ..writer import get_writer
from .runner import AbstractRunner


class CLIRunner(AbstractRunner):
    """Resilient CLI runner used by OLYA bulk imports.

    Upstream parser-2gis aborts the whole URL batch if one rubric raises. For
    city-wide collection we need each URL to be isolated: log the failed rubric
    and continue with the rest while keeping already written records.
    """

    def start(self):
        logger.info('Парсинг запущен.')
        try:
            with get_writer(self._output_path, self._format, self._config.writer) as writer:
                for url in self._urls:
                    logger.info(f'Парсинг ссылки {url}')
                    try:
                        with get_parser(
                            url,
                            chrome_options=self._config.chrome,
                            parser_options=self._config.parser,
                        ) as parser:
                            parser.parse(writer)
                    except (KeyboardInterrupt, ChromeUserAbortException):
                        raise
                    except Exception as exc:
                        if isinstance(exc, ChromeRuntimeException) and str(exc) == 'Tab has been stopped':
                            logger.error('Вкладка браузера была закрыта.')
                        else:
                            logger.error('Ошибка парсинга ссылки; продолжаю следующую рубрику.', exc_info=True)
                    finally:
                        logger.info('Парсинг ссылки завершён.')
        except (KeyboardInterrupt, ChromeUserAbortException):
            logger.error('Работа парсера прервана пользователем.')
        finally:
            logger.info('Парсинг завершён.')

    def stop(self):
        pass
