"""Optional Neo4j graph of who owns which vehicle, what happened to it, who it was shared with and who viewed it.
Active only when NEO4J_URI is set. Failures are logged and never break the request."""
import logging, os

log = logging.getLogger("roadrec.graph")
_driver = None


def enabled() -> bool:
    return bool(os.getenv("NEO4J_URI"))


def _run(cypher: str, **params):
    global _driver
    if not enabled():
        return
    try:
        if _driver is None:
            from neo4j import GraphDatabase
            _driver = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                           auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")))
        with _driver.session() as s:
            s.run(cypher, **params)
    except Exception as e:  # pragma: no cover
        log.warning("neo4j write skipped: %s", e)


def record_upload(user, vehicle, ev):
    _run("""MERGE (u:User {id:$uid}) SET u.email=$email, u.role=$role
            MERGE (v:Vehicle {id:$vid}) SET v.reg_no=$reg
            MERGE (u)-[:OWNS]->(v)
            MERGE (e:Event {id:$eid}) SET e.verdict=$verdict, e.sha256=$sha, e.recorded_at=$at
            MERGE (v)-[:HAD_EVENT]->(e)""",
         uid=user.id, email=user.email, role=user.role, vid=vehicle.id, reg=vehicle.reg_no,
         eid=ev.id, verdict=ev.verdict, sha=ev.sha256, at=ev.recorded_at.isoformat())


def record_share(user, ev, link):
    _run("""MATCH (e:Event {id:$eid}) MERGE (l:ShareLink {id:$lid}) SET l.expires_at=$exp
            MERGE (e)-[:SHARED_VIA]->(l)""", eid=ev.id, lid=link.id, exp=link.expires_at.isoformat())


def record_view(user, ev):
    _run("""MERGE (u:User {id:$uid}) SET u.email=$email, u.role=$role
            WITH u MATCH (e:Event {id:$eid}) MERGE (u)-[r:VIEWED]->(e) SET r.last_at=datetime()""",
         uid=user.id, email=user.email, role=user.role, eid=ev.id)
