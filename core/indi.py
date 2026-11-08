import socket

def testar_conexao_indi(host="127.0.0.1", port="7624", timeout=3):
    """
    Testa a conectividade com o servidor INDI e regista no app.log.
    """
    print(f"[INDI LOG] Testing connection to INDI server at {host}:{port}...")
    try:
        port_num = int(port)
        s = socket.create_connection((host, port_num), timeout=timeout)
        s.close()
        msg = f"INDI Server Online ({host}:{port})"
        print(f"[INDI LOG] SUCCESS: {msg}")
        return True, msg
    except Exception as e:
        msg = f"INDI Offline: {str(e)}"
        print(f"[INDI LOG] ERROR: {msg}")
        return False, msg

def enviar_slew_indi(host, port, ra_str, dec_str, timeout=5):
    """
    Envia o comando de Slew para o servidor INDI via TCP/XML e regista no app.log.
    """
    print(f"[INDI LOG] Dispatching Slew command -> Target RA: {ra_str} | Dec: {dec_str} to INDI Server {host}:{port}")
    try:
        port_num = int(port)
        s = socket.create_connection((host, port_num), timeout=timeout)
        xml_command = f'<newNumberVector device="Telescope" name="EQUATORIAL_EOD"><oneNumber name="RA">{ra_str}</oneNumber><oneNumber name="DEC">{dec_str}</oneNumber></newNumberVector>\n'
        s.sendall(xml_command.encode('utf-8'))
        s.close()
        msg = f"Slew command successfully sent to INDI Server ({host}:{port})"
        print(f"[INDI LOG] SUCCESS: {msg}")
        return True, msg
    except Exception as e:
        msg = f"INDI Connection Error: {str(e)}"
        print(f"[INDI LOG] ERROR: {msg}")
        return False, msg