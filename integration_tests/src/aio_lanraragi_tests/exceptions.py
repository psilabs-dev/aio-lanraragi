

class DeploymentException(Exception):
    """
    Exception raised when a deployment operation fails.
    """
    
    def __init__(self, message):
        super().__init__(message)
        pass
