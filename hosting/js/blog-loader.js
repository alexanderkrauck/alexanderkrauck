// Blog Loader for Markdown Files
class BlogLoader {
    constructor() {
        this.posts = [];
        this.markdownConverter = new showdown.Converter({
            tables: true,
            strikethrough: true,
            tasklists: true,
            ghCodeBlocks: true,
            smoothLivePreview: true,
            simpleLineBreaks: true
        });
    }

    async loadBlogPosts() {
        // In a real implementation, you'd have a server endpoint that lists blog files
        // For now, we'll use a predefined list based on our file naming convention
        const blogFiles = [
            '2024-12-01-ai-revolution-in-industry.md',
            '2024-11-15-graph-neural-networks-breakthrough.md',
            '2024-10-20-mlops-enterprise-deployment.md'
        ];

        try {
            const posts = await Promise.all(
                blogFiles.map(async (filename) => {
                    try {
                        const response = await fetch(`/blog/${filename}`);
                        if (!response.ok) throw new Error(`Failed to load ${filename}`);
                        
                        const content = await response.text();
                        return this.parseMarkdownPost(content, filename);
                    } catch (error) {
                        console.warn(`Could not load blog post: ${filename}`, error);
                        return null;
                    }
                })
            );

            this.posts = posts
                .filter(post => post !== null)
                .sort((a, b) => new Date(b.date) - new Date(a.date));

            return this.posts;
        } catch (error) {
            console.error('Error loading blog posts:', error);
            return this.getFallbackPosts();
        }
    }

    parseMarkdownPost(content, filename) {
        // Extract metadata from filename (YYYY-MM-DD-title.md format)
        const dateMatch = filename.match(/^(\d{4}-\d{2}-\d{2})-(.+)\.md$/);
        const date = dateMatch ? dateMatch[1] : new Date().toISOString().split('T')[0];
        const slug = dateMatch ? dateMatch[2] : filename.replace('.md', '');

        // Parse markdown content
        const lines = content.split('\n');
        const title = lines[0].replace(/^#\s*/, '');
        
        // Extract metadata from the content
        let author = 'Alexander Krauck';
        let tags = [];
        let excerpt = '';

        // Look for metadata in the first few lines
        for (let i = 1; i < Math.min(10, lines.length); i++) {
            const line = lines[i];
            if (line.includes('*Author:')) {
                author = line.replace(/.*\*Author:\s*/, '').replace(/\*.*/, '');
            }
            if (line.includes('*Tags:')) {
                tags = line.replace(/.*\*Tags:\s*/, '').replace(/\*.*/, '').split(',').map(t => t.trim());
            }
        }

        // Generate excerpt (first paragraph after metadata)
        const contentStart = lines.findIndex(line => line.trim() === '---');
        if (contentStart !== -1 && contentStart + 1 < lines.length) {
            const paragraphStart = contentStart + 1;
            for (let i = paragraphStart; i < lines.length; i++) {
                if (lines[i].trim() && !lines[i].startsWith('#') && !lines[i].startsWith('*')) {
                    excerpt = lines[i].trim();
                    break;
                }
            }
        }

        // Convert markdown to HTML
        const html = this.markdownConverter.makeHtml(content);

        return {
            title,
            slug,
            date,
            author,
            tags,
            excerpt: excerpt || 'Click to read more...',
            content,
            html,
            readTime: this.calculateReadTime(content)
        };
    }

    calculateReadTime(content) {
        const wordsPerMinute = 200;
        const words = content.split(/\s+/).length;
        const minutes = Math.ceil(words / wordsPerMinute);
        return `${minutes} min read`;
    }

    getFallbackPosts() {
        return [
            {
                title: 'The AI Revolution in Industrial Applications',
                slug: 'ai-revolution-in-industry',
                date: '2024-12-01',
                author: 'Alexander Krauck',
                tags: ['AI', 'Industry', 'MLOps', 'Innovation'],
                excerpt: 'The industrial landscape is undergoing a seismic shift. As Head of the AI Competence Center at Fabasoft, I\'ve witnessed firsthand how artificial intelligence is transforming traditional manufacturing processes.',
                readTime: '5 min read'
            },
            {
                title: 'Graph Neural Networks: The Next Frontier in AI',
                slug: 'graph-neural-networks-breakthrough',
                date: '2024-11-15',
                author: 'Alexander Krauck',
                tags: ['GNN', 'Research', 'Deep Learning', 'Graph Theory'],
                excerpt: 'During my research at Johannes Kepler University, I delved deep into Graph Neural Networks (GNNs) and their transformative potential.',
                readTime: '7 min read'
            },
            {
                title: 'MLOps at Scale: Enterprise Deployment Strategies',
                slug: 'mlops-enterprise-deployment',
                date: '2024-10-20',
                author: 'Alexander Krauck',
                tags: ['MLOps', 'Enterprise', 'Deployment', 'DevOps', 'AI'],
                excerpt: 'Deploying machine learning models in enterprise environments presents unique challenges that go far beyond the typical data science workflow.',
                readTime: '8 min read'
            }
        ];
    }

    renderBlogPosts(containerId, limit = 3) {
        const container = document.getElementById(containerId);
        if (!container) return;

        const postsToShow = this.posts.slice(0, limit);

        container.innerHTML = postsToShow.map(post => `
            <article class="blog-post-card">
                <div class="post-header">
                    <h3 class="post-title">
                        <a href="#" onclick="blogLoader.openPost('${post.slug}')" class="post-link">
                            ${post.title}
                        </a>
                    </h3>
                    <div class="post-meta">
                        <span class="post-date">${this.formatDate(post.date)}</span>
                        <span class="post-read-time">${post.readTime}</span>
                    </div>
                </div>
                
                <p class="post-excerpt">${post.excerpt}</p>
                
                <div class="post-footer">
                    <div class="post-tags">
                        ${post.tags.slice(0, 3).map(tag => 
                            `<span class="tag">${tag}</span>`
                        ).join('')}
                    </div>
                    <button class="read-more-btn" onclick="blogLoader.openPost('${post.slug}')">
                        READ_MORE >>
                    </button>
                </div>
            </article>
        `).join('');

        this.addBlogAnimations();
    }

    formatDate(dateString) {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'long',
            day: 'numeric'
        });
    }

    openPost(slug) {
        const post = this.posts.find(p => p.slug === slug);
        if (!post) return;

        // Create modal overlay
        const modal = document.createElement('div');
        modal.className = 'blog-modal';
        modal.innerHTML = `
            <div class="blog-modal-content">
                <div class="blog-modal-header">
                    <h1>${post.title}</h1>
                    <button class="close-modal" onclick="this.closest('.blog-modal').remove()">×</button>
                </div>
                <div class="blog-modal-meta">
                    <span>${this.formatDate(post.date)}</span>
                    <span>•</span>
                    <span>${post.author}</span>
                    <span>•</span>
                    <span>${post.readTime}</span>
                </div>
                <div class="blog-modal-body">
                    ${post.html || this.markdownConverter.makeHtml(post.content || post.excerpt)}
                </div>
            </div>
        `;

        document.body.appendChild(modal);
        
        // Add click outside to close
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.remove();
            }
        });

        // Add escape key to close
        const escapeHandler = (e) => {
            if (e.key === 'Escape') {
                modal.remove();
                document.removeEventListener('keydown', escapeHandler);
            }
        };
        document.addEventListener('keydown', escapeHandler);
    }

    addBlogAnimations() {
        const cards = document.querySelectorAll('.blog-post-card');
        cards.forEach((card, index) => {
            card.style.animationDelay = `${index * 0.1}s`;
            
            card.addEventListener('mouseenter', () => {
                card.style.transform = 'translateY(-5px)';
                card.style.boxShadow = '0 15px 30px rgba(0,255,136,0.2)';
            });
            
            card.addEventListener('mouseleave', () => {
                card.style.transform = 'translateY(0)';
                card.style.boxShadow = '0 5px 15px rgba(0,255,136,0.1)';
            });
        });
    }

    async init(containerId, limit = 3) {
        // Show loading state
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = `
                <div class="loading-blog">
                    <div class="loading-spinner"></div>
                    <div class="loading-text">LOADING NEURAL BLOG ENTRIES...</div>
                </div>
            `;
        }

        // Load and render posts
        await this.loadBlogPosts();
        this.renderBlogPosts(containerId, limit);
    }
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    // Load showdown.js for markdown parsing
    if (typeof showdown === 'undefined') {
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/showdown@2.1.0/dist/showdown.min.js';
        script.onload = () => {
            window.blogLoader = new BlogLoader();
            blogLoader.init('blog-posts');
        };
        document.head.appendChild(script);
    } else {
        window.blogLoader = new BlogLoader();
        blogLoader.init('blog-posts');
    }
});

// Export for use in other modules
window.BlogLoader = BlogLoader; 