import sys
import io
import pathlib
import traceback
import ast
import contextlib
from colorama import Fore, Style
from typing import Dict, Any, Optional, List, Callable
from pathlib import Path

from config import logging_config
import logging

class CodeExecutor:
    """
    A simplified code execution environment with state management,
    output capture, error handling, and restricted filesystem access.
    """
    
    def __init__(
        self,
        predefined_functions: Optional[Dict[str, Callable]] = None,
        allowed_filesystem_paths: Optional[List[str]] = None,
        allowed_file_modes: Optional[List[str]] = None,
        max_output_size: int = 10000
    ):
        """
        Initialize the code executor.
        
        Args:
            predefined_functions: Dictionary of function_name -> function to make available
            allowed_filesystem_paths: List of absolute directory paths where file operations are allowed. 
                                     If None, no file operations are allowed.
            allowed_file_modes: List of allowed file modes ('r', 'w', 'a', etc.). 
                              If None, all modes are allowed (subject to path restrictions).
            max_output_size: Maximum size of captured output in characters
        """
        self.max_output_size = max_output_size
        self.allowed_filesystem_paths = self._ensure_all_absolute(allowed_filesystem_paths)
        self.allowed_file_modes = allowed_file_modes

        # Initialize persistent state - use single namespace for module-level execution
        self.namespace = self._create_namespace()
        
        # Add predefined functions to the namespace
        if predefined_functions:
            for name, func in predefined_functions.items():
                self.namespace[name] = func
        
        # Track execution history
        self.execution_history = []
        
    def _ensure_all_absolute(self, paths: Optional[List[str]]) -> List[str]:
        """
        Ensure all paths are absolute. Raise ValueError if any path is not absolute.
        
        Args:
            paths: List of paths to validate
            
        Returns:
            List of absolute paths
            
        Raises:
            ValueError: If any path is not absolute
        """
        if not paths:
            return []

        not_absolute = [p for p in paths if not Path(p).is_absolute()]
        if not_absolute:
            raise ValueError(
                "The following paths are not absolute: " + ", ".join(not_absolute)
            )
        return paths
        
    def _create_namespace(self) -> Dict[str, Any]:
        """
        Create a namespace with standard Python builtins but restricted file access.
        
        Returns:
            Dictionary to use as both globals and locals for code execution
        """
        import builtins
        
        # Start with all standard builtins
        namespace = {}
        
        # Copy all builtins, including essential dunder methods
        for name in dir(builtins):
            namespace[name] = getattr(builtins, name)
        
        # Override open with our restricted version
        namespace['open'] = self._restricted_open
        
        # Set up module-level variables
        namespace['__name__'] = '__main__'
        namespace['__doc__'] = None
        
        return namespace
    
    def _restricted_open(self, file, mode='r', *args, **kwargs):
        """
        Restricted version of open() that only allows access to specified paths.
        
        Args:
            file: Path to the file to open
            mode: File mode ('r', 'w', 'a', etc.)
            *args, **kwargs: Additional arguments to pass to open()
        
        Returns:
            File handle if access is allowed
            
        Raises:
            PermissionError: If file access is not allowed
        """
        # If no paths are allowed, deny all file operations
        if not self.allowed_filesystem_paths:
            raise PermissionError(
                "File operations are not allowed in this execution environment"
            )
        
        # Resolve the requested path (handles relative paths, symlinks, etc.)
        try:
            requested_path = pathlib.Path(file).resolve()
        except Exception as e:
            raise PermissionError(f"Invalid file path '{file}': {e}") from e
        
        # Check if file mode is allowed
        if self.allowed_file_modes is not None:
            # Extract base mode (remove modifiers like '+', 'b', 't')
            base_mode = mode.replace('+', '').replace('b', '').replace('t', '')
            if mode not in self.allowed_file_modes and base_mode not in self.allowed_file_modes:
                raise PermissionError(
                    f"File mode '{mode}' is not allowed. "
                    f"Allowed modes: {', '.join(self.allowed_file_modes)}"
                )
        
        # Check if the resolved path is within any allowed directory
        path_allowed = False
        for allowed_path in self.allowed_filesystem_paths:
            try:
                # Check if requested path is within allowed directory
                requested_path.relative_to(pathlib.Path(allowed_path))
                path_allowed = True
                break
            except ValueError:
                # Not within this allowed path, continue checking others
                continue
        
        if not path_allowed:
            raise PermissionError(
                f"Access to '{requested_path}' is not allowed. "
                f"File operations are restricted to: {', '.join(self.allowed_filesystem_paths)}"
            )
        
        # If we get here, the file access is allowed - perform the actual open
        # Use the built-in open, not our restricted version to avoid recursion
        return __builtins__['open'](file, mode, *args, **kwargs)
    
    def execute(self, code: str) -> Dict[str, Any]:
        """
        Execute Python code in the sandboxed environment.
        
        Args:
            code: Python code string to execute
            
        Returns:
            Dictionary containing execution results:
                - success: Boolean indicating if execution succeeded
                - output: Captured stdout/stderr output
                - result: Return value for expressions, None for statements
                - error: Error message if execution failed
                - traceback: Full traceback string if an error occurred
        """
        # Set up output capture
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        result = None
        error_msg = None
        tb_str = None
        
        @contextlib.contextmanager
        def capture_output():
            """Context manager to capture stdout and stderr."""
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            try:
                sys.stdout = stdout_capture
                sys.stderr = stderr_capture
                yield
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr
        
        try:
            # First, check for syntax errors
            ast.parse(code)
            
            # Execute the code with output capture
            with capture_output():
                # Try to evaluate as an expression first (for interactive-style execution)
                try:
                    result = eval(code, self.namespace, self.namespace)
                except SyntaxError:
                    # Not an expression, execute as statements
                    # Use same namespace for both globals and locals (crucial for module-level code)
                    exec(code, self.namespace, self.namespace)
                    result = None
            
            # Collect captured output
            output = stdout_capture.getvalue() + stderr_capture.getvalue()
            
            # Truncate output if it's too large
            if len(output) > self.max_output_size:
                output = output[:self.max_output_size] + "\n... (output truncated)"
            
            execution_record = {
                'success': True,
                'output': output,
                'result': result,
                'error': None,
                'traceback': None
            }
            
        except Exception as e:
            # Capture error information
            error_msg = str(e)
            tb_str = traceback.format_exc()
            
            # Get any partial output that was produced before the error
            output = stdout_capture.getvalue() + stderr_capture.getvalue()
            
            execution_record = {
                'success': False,
                'output': output,
                'result': None,
                'error': error_msg,
                'traceback': tb_str
            }
        
        # Add this execution to the history
        self.execution_history.append({
            'code': code,
            'result': execution_record
        })
        
        return execution_record
    
    def reset_state(self):
        """Reset the execution environment to its initial state."""
        self.namespace = self._create_namespace()
        self.execution_history = []
    
    def get_state(self) -> Dict[str, Any]:
        """
        Get the current state of the execution environment.
        
        Returns:
            Dictionary with current variables (excluding private/builtin names)
        """
        # Return only user-defined variables, excluding builtins and private names
        user_vars = {
            name: value for name, value in self.namespace.items() 
            if not name.startswith('__') and name not in dir(__builtins__)
        }
        return {'variables': user_vars}
    
    def set_variable(self, name: str, value: Any):
        """
        Set a variable in the execution environment.
        
        Args:
            name: Variable name
            value: Variable value
        """
        self.namespace[name] = value
    
    def get_variable(self, name: str, default=None):
        """
        Get a variable from the execution environment.
        
        Args:
            name: Variable name
            default: Default value if variable doesn't exist
            
        Returns:
            Variable value or default
        """
        return self.namespace.get(name, default)
    
    def add_function(self, name: str, func: Callable):
        """
        Add a function to the execution environment.
        
        Args:
            name: Function name
            func: Function object
        """
        self.namespace[name] = func
    
    def get_history(self) -> List[Dict[str, Any]]:
        """
        Get the execution history.
        
        Returns:
            List of execution records
        """
        return self.execution_history.copy()
    
    def list_variables(self) -> List[str]:
        """
        Get a list of user-defined variable names.
        
        Returns:
            List of variable names (excluding builtins and private names)
        """
        return [
            name for name in self.namespace.keys()
            if not name.startswith('__') and name not in dir(__builtins__)
        ]

if __name__== "__main__":
    logging.info(f"{Fore.BLUE}{Style.BRIGHT}[{__name__}] Running Code Executor for sample python code{Style.RESET_ALL}")
    sample_code = """from datetime import datetime
from colorama import Fore, Style
now = datetime.now()
dt_string = now.strftime("%Y-%m-%d %H:%M:%S")
print(f"{Fore.GREEN}{Style.BRIGHT}[Code Executor] {Fore.BLACK}Current Time: {dt_string}{Style.RESET_ALL}")
"""
    executor = CodeExecutor()
    output = executor.execute(sample_code)
    logging.info(output['output'])
