import json
import logging
import sys

def setup_structured_logging():
    """Configures the root logger to output structured JSON."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    handler = logging.StreamHandler(sys.stdout)
    
    class StructuredFormatter(logging.Formatter):
        def format(self, record):
            # Extract basic log information
            log_data = {
                "timestamp": self.formatTime(record, self.datefmt),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            
            # Extract context variables from 'extra'
            if hasattr(record, 'context'):
                log_data.update(record.context)
                
            # Exclude noisy uvicorn logs if necessary, or just keep them structured
            return json.dumps(log_data)
            
    handler.setFormatter(StructuredFormatter())
    logger.addHandler(handler)

class StructuredLogger:
    """Wrapper to easily log with context dictionaries."""
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        
    def info(self, msg: str, *args, **context):
        self.logger.info(msg, *args, extra={"context": context} if context else {})
        
    def error(self, msg: str, *args, **context):
        self.logger.error(msg, *args, extra={"context": context} if context else {})
        
    def warning(self, msg: str, *args, **context):
        self.logger.warning(msg, *args, extra={"context": context} if context else {})
        
    def debug(self, msg: str, *args, **context):
        self.logger.debug(msg, *args, extra={"context": context} if context else {})

def get_logger(name: str) -> StructuredLogger:
    return StructuredLogger(name)
