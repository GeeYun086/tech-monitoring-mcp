"""python -m tech_monitoring.mcp_server 로 띄우기 위한 진입점.

MCP 클라이언트 설정에 파일 경로 대신 모듈 경로를 적을 수 있어, 저장소 위치가
달라져도 설정을 고칠 필요가 없다.
"""

from tech_monitoring.mcp_server.server import main

if __name__ == "__main__":
    main()
