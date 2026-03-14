<?xml version="1.0" encoding="UTF-8"?>
<xsl:stylesheet version="1.0"
                xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
                xmlns:content="http://purl.org/rss/1.0/modules/content/"
                xmlns:atom="http://www.w3.org/2005/Atom"
                xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <xsl:output method="html" encoding="UTF-8" indent="yes" />

  <xsl:template match="/">
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>
          <xsl:choose>
            <xsl:when test="rss/channel/title">
              <xsl:value-of select="rss/channel/title" />
            </xsl:when>
            <xsl:otherwise>
              <xsl:value-of select="atom:feed/atom:title" />
            </xsl:otherwise>
          </xsl:choose>
        </title>
        <style>
          html {
            color-scheme: light dark;
          }

          body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif,
              "Apple Color Emoji", "Segoe UI Emoji", "Segoe UI Symbol";
            line-height: 1.4;
            padding: 0;
            font-size: 115%;
            max-width: 75%;
            margin: 0 auto;
          }

          @media (max-width: 900px) {
            body {
              max-width: 95%;
            }
          }

          h1 {
            margin: 1rem 0 0.25rem;
          }

          .meta {
            margin-bottom: 1.5rem;
          }

          .item {
            border: 1px solid color-mix(in srgb, Canvas 85%, CanvasText 15%);
            padding: 1rem;
            margin-bottom: 0.75rem;
          }

          .item h2 {
            margin: 0 0 0.35rem;
            font-size: 1.05rem;
          }

          .pubdate {
            color: color-mix(in srgb, CanvasText 60%, Canvas 40%);
            font-size: 0.9rem;
            margin-bottom: 0.6rem;
          }

          .item-content img {
            max-width: 100%;
            height: auto;
          }

          .item-content-frame {
            width: 100%;
            min-height: 24rem;
            height: 28rem;
            border: 0;
            background: transparent;
            display: block;
            overflow: hidden;
          }
        </style>
      </head>
      <body>
        <h1>
          <xsl:choose>
            <xsl:when test="rss/channel/title">
              <xsl:value-of select="rss/channel/title" />
            </xsl:when>
            <xsl:otherwise>
              <xsl:value-of select="atom:feed/atom:title" />
            </xsl:otherwise>
          </xsl:choose>
        </h1>
        <p class="meta">
          <xsl:choose>
            <xsl:when test="rss/channel/description">
              <xsl:value-of select="rss/channel/description" />
            </xsl:when>
            <xsl:otherwise>
              <xsl:value-of select="atom:feed/atom:subtitle" />
            </xsl:otherwise>
          </xsl:choose>
        </p>

        <xsl:for-each select="rss/channel/item | atom:feed/atom:entry">
          <article class="item">
            <h2>
              <a href="{link | atom:link[@rel='alternate'][1]/@href | atom:link[not(@rel)][1]/@href}">
                <xsl:value-of select="title" />
                <xsl:value-of select="atom:title" />
              </a>
            </h2>
            <p class="pubdate">
              <xsl:value-of select="pubDate | atom:published | atom:updated" />
            </p>
            <div class="item-content">
              <iframe class="item-content-frame" loading="lazy" sandbox="allow-popups allow-popups-to-escape-sandbox allow-same-origin">
                <xsl:attribute name="srcdoc">
                  <xsl:text>&lt;!doctype html&gt;&lt;html lang='en'&gt;&lt;head&gt;&lt;meta charset='utf-8' /&gt;&lt;meta name='viewport' content='width=device-width, initial-scale=1' /&gt;&lt;style&gt;html{color-scheme:light dark;}body{margin:0;padding:0;font-family:-apple-system,BlinkMacSystemFont,&quot;Segoe UI&quot;,Roboto,Helvetica,Arial,sans-serif,&quot;Apple Color Emoji&quot;,&quot;Segoe UI Emoji&quot;,&quot;Segoe UI Symbol&quot;;line-height:1.4;background:Canvas;color:CanvasText;}img{max-width:100%;height:auto;}&lt;/style&gt;&lt;/head&gt;&lt;body&gt;</xsl:text>
                  <xsl:choose>
                    <xsl:when test="content:encoded">
                      <xsl:value-of select="content:encoded" />
                    </xsl:when>
                    <xsl:when test="atom:content">
                      <xsl:value-of select="atom:content" />
                    </xsl:when>
                    <xsl:when test="atom:summary">
                      <xsl:value-of select="atom:summary" />
                    </xsl:when>
                    <xsl:otherwise>
                      <xsl:value-of select="description" />
                    </xsl:otherwise>
                  </xsl:choose>
                  <xsl:text>&lt;/body&gt;&lt;/html&gt;</xsl:text>
                </xsl:attribute>
              </iframe>
            </div>
          </article>
        </xsl:for-each>
      </body>
    </html>
  </xsl:template>
</xsl:stylesheet>
