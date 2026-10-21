def create_driver(config):
    if config.get('backend') == 'qqnt_private':
        from .qqnt import QQNTPrivateDesktop
        return QQNTPrivateDesktop(config)
    from .atspi import AtspiDesktop
    return AtspiDesktop(config)
